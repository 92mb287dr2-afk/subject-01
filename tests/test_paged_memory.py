import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest

from subject01.candidate import CandidateRuntime
from subject01.continuity import ContinuityStore, DiskMemories, digest
from subject01.life import LifeCore


class PagedMemoryTests(unittest.TestCase):
    def test_disk_and_list_recall_produce_identical_future(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = CandidateRuntime.open(folder)
            try:
                runtime.advance()
                expected = LifeCore.from_state(runtime.core.state())
                for _ in range(200):
                    expected.step()
                    runtime.advance()
                self.assertEqual(runtime.core.state(), expected.state())
                self.assertIsInstance(runtime.core.memories, DiskMemories)
                self.assertLessEqual(len(runtime.store.memory_cache), 128)
            finally:
                runtime.stop()

    def test_large_restore_and_steps_have_bounded_python_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = CandidateRuntime.open(folder)
            runtime.advance()
            state = runtime.status()
            runtime.stop()
            template = state["life"]["memories"][0]
            records = []
            for i in range(26000):
                record = {**template, "memory_id": i + 1}
                record["integrity_hash"] = digest({k: v for k, v in record.items() if k != "integrity_hash"})
                records.append(record)
            state["life"]["memories"] = records
            state["life"]["next_memory_id"] = 26001
            store = ContinuityStore(folder)
            store.commit(state, [{"kind": "storage_fixture"}])
            store.close()
            del state, records, template
            tracemalloc.start()
            runtime = CandidateRuntime.open(folder)
            try:
                self.assertEqual(len(runtime.core.memories), 26000)
                self.assertLess(tracemalloc.get_traced_memory()[1], 4 * 1024 * 1024)
                statements = []
                runtime.store.db.set_trace_callback(statements.append)
                runtime.advance()
                runtime.store.db.set_trace_callback(None)
                self.assertLess(len(statements), 100)
                self.assertLessEqual(len(runtime.store.memory_cache), 128)
                page = runtime.status(memory_limit=100)
                self.assertEqual(len(page["life"]["memories"]), 100)
                self.assertEqual(page["life"]["memory_page"]["total"], 26000)
                self.assertEqual(runtime.memory_page(25999)["records"][0]["memory_id"], 26000)
            finally:
                runtime.stop()
                tracemalloc.stop()

    def test_export_snapshot_is_stable_across_confirmed_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = CandidateRuntime.open(folder)
            try:
                runtime.advance()
                before = runtime.core.memories[0]
                with runtime.store.memory_export() as (manifest, records):
                    grant = runtime.preview_override("delete_memory", 1)
                    runtime.confirm_override(grant["token"], grant["required_confirmation"])
                    self.assertEqual(list(records), [before])
                    self.assertEqual(manifest["memory_manifest"]["count"], 1)
                self.assertEqual(len(runtime.core.memories), 0)
                runtime.advance()
                self.assertEqual(runtime.core.memories[0]["memory_id"], 2)
                runtime.save_snapshot()
            finally:
                runtime.stop()


if __name__ == "__main__":
    unittest.main()
