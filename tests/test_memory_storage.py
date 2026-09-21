"""Protected memory storage: compact checkpoints, migration and crash recovery."""
import hashlib
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zlib

from subject01.candidate import CandidateRuntime
from subject01.continuity import ContinuityStore, digest, encode


class MemoryStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def seed(self):
        runtime = CandidateRuntime.open(self.path)
        runtime.advance()
        state = runtime.status()
        runtime.stop()
        return state

    def legacy(self):
        state = self.seed()
        store = ContinuityStore(self.path)
        raw = encode(state)
        with store.transaction():
            store.db.execute("DROP TABLE memories")
            store.db.execute("DROP TABLE journal_archives")
            store.db.execute("UPDATE checkpoint SET payload=?,hash=? WHERE id=1",
                             (zlib.compress(raw), hashlib.sha256(raw).hexdigest()))
            store.db.execute("PRAGMA user_version=1")
        store.close()
        return state

    def test_large_archive_does_not_consume_checkpoint_budget_or_rewrite_old_rows(self):
        state = self.seed()
        template = state["life"]["memories"][0]
        # More than the old 8 MiB checkpoint ceiling, using real numeric records.
        records = []
        for i in range(26000):
            record = {**template, "memory_id": i + 1}
            record["integrity_hash"] = digest({k: v for k, v in record.items() if k != "integrity_hash"})
            records.append(record)
        state["life"]["memories"] = records
        self.assertGreater(len(encode(state)), 8 * 1024 * 1024)
        store = ContinuityStore(self.path)
        try:
            store.commit(state, [{"kind": "storage_fixture"}])
            payload = store.db.execute("SELECT payload FROM checkpoint").fetchone()[0]
            compact = json.loads(zlib.decompress(payload))
            self.assertNotIn("memories", compact["life"])
            self.assertEqual(compact["life"]["memory_manifest"]["count"], len(records))
            self.assertLess(len(zlib.decompress(payload)), 100000)
            statements = []
            store.db.set_trace_callback(statements.append)
            store.commit(state, [])
            store.db.set_trace_callback(None)
            self.assertFalse(any("INTO memories" in q or "DELETE FROM memories" in q for q in statements))
            self.assertEqual(store.load(), state)
        finally:
            store.close()

    def test_missing_or_changed_memory_is_not_silently_accepted(self):
        self.seed()
        store = ContinuityStore(self.path)
        try:
            with self.assertRaisesRegex(ValueError, "boundary mismatch"):
                with store.transaction():
                    store.db.execute("DELETE FROM memories")
                    store.load()
            with self.assertRaisesRegex(ValueError, "checksum"):
                with store.transaction():
                    store.db.execute("UPDATE memories SET hash='invalid'")
                    store.load()
            # Caller mutation with a stale integrity hash must not evade the cache.
            state = store.load()
            state["life"]["memories"][0]["representation"]["sensors"][0] = .123456
            with self.assertRaisesRegex(ValueError, "integrity"):
                store.commit(state, [])
        finally:
            store.close()

    def test_legacy_open_does_not_migrate_and_explicit_migration_preserves_identity(self):
        before = self.legacy()
        store = ContinuityStore(self.path)
        try:
            self.assertEqual(store.format, 1)
            self.assertEqual(store.load(), before)
            journal = store.journal()
            store.migrate_memory_layout()
            self.assertEqual(store.format, 2)
            self.assertEqual(store.load(), before)
            self.assertEqual(store.journal(), journal)
            store.migrate_memory_layout()  # Idempotent, not another history event.
            self.assertEqual(store.load(), before)
        finally:
            store.close()

    def test_real_crashes_during_migration_keep_whole_old_or_new_layout(self):
        code = """
import os, sys
from subject01.continuity import ContinuityStore
store = ContinuityStore(sys.argv[1])
store.fault_hook = lambda phase: os._exit(77) if phase == sys.argv[2] else None
store.migrate_memory_layout()
"""
        root = self.path
        for phase in ("after_begin", "after_memories", "after_checkpoint", "before_commit", "after_commit"):
            with self.subTest(phase=phase):
                self.path = root / phase
                before = self.legacy()
                process = subprocess.run([sys.executable, "-c", code, str(self.path), phase],
                                         capture_output=True, timeout=30)
                self.assertEqual(process.returncode, 77, process.stderr.decode())
                store = ContinuityStore(self.path)
                try:
                    self.assertEqual(store.format, 2 if phase == "after_commit" else 1)
                    self.assertEqual(store.load(), before)
                    self.assertEqual(store.verify_journal(), before["continuity"]["journal_hash"])
                finally:
                    store.close()
        self.path = root

    def archive_fixture(self):
        state = self.seed()
        store = ContinuityStore(self.path)
        try:
            for _ in range(509):
                store.commit(state, [{"kind": "repeated_storage_fixture", "values": [0.1] * 32}])
            self.assertEqual(store.journal(510)[0]["seq"], 511)
            return state
        finally:
            store.close()

    def test_archive_keeps_all_events_chain_and_cross_boundary_pagination(self):
        state = self.archive_fixture()
        store = ContinuityStore(self.path)
        try:
            before = store.journal(0, 500) + store.journal(500, 500)
            store.commit(state, [{"kind": "archive_trigger"}])
            self.assertEqual(store.db.execute("SELECT count(*) FROM journal_archives").fetchone()[0], 1)
            self.assertEqual(store.db.execute("SELECT count(*) FROM journal").fetchone()[0], 256)
            after = store.journal(0, 500) + store.journal(500, 500)
            self.assertEqual(after[:-1], before)
            self.assertEqual(store.journal(250, 20), after[250:270])
            self.assertEqual(store.verify_journal(), state["continuity"]["journal_hash"])
            store.db.execute("UPDATE journal_archives SET hash='corrupted'")
            with self.assertRaisesRegex(ValueError, "archive checksum"):
                store.journal()
        finally:
            store.close()

    def test_real_crashes_during_archiving_lose_no_events(self):
        before = self.archive_fixture()
        code = """
import os, sys
from subject01.continuity import ContinuityStore
store = ContinuityStore(sys.argv[1])
state = store.load()
store.fault_hook = lambda phase: os._exit(77) if phase == sys.argv[2] else None
store.commit(state, [{"kind": "archive_trigger"}])
"""
        for phase in ("after_archive_insert", "after_archive_delete", "before_commit", "after_commit"):
            with self.subTest(phase=phase):
                folder = self.path / phase
                folder.mkdir()
                shutil.copyfile(self.path / "continuity.sqlite3", folder / "continuity.sqlite3")
                process = subprocess.run([sys.executable, "-c", code, str(folder), phase],
                                         capture_output=True, timeout=30)
                self.assertEqual(process.returncode, 77, process.stderr.decode())
                store = ContinuityStore(folder)
                try:
                    committed = phase == "after_commit"
                    batches = store.journal(0, 500) + store.journal(500, 500)
                    self.assertEqual(len(batches), 512 if committed else 511)
                    self.assertEqual([b["seq"] for b in batches], list(range(1, len(batches) + 1)))
                    self.assertEqual(store.verify_journal(), store.load()["continuity"]["journal_hash"])
                    if not committed:
                        self.assertEqual(store.load(), before)
                finally:
                    store.close()


if __name__ == "__main__":
    unittest.main()
