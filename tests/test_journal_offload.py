from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from subject01.candidate import CandidateRuntime
from subject01.continuity import ContinuityStore


class JournalOffloadTests(unittest.TestCase):
    def fixture(self, folder):
        runtime = CandidateRuntime.open(folder)
        runtime.advance()
        state = runtime.status()
        runtime.stop()
        store = ContinuityStore(folder)
        for _ in range(100):
            store.commit(state, [{"kind": "archive_fixture"}])
        expected = store.journal(0, 500)
        store.close()
        return state, expected

    def test_offload_restart_and_missing_or_corrupt_pack(self):
        with tempfile.TemporaryDirectory() as root:
            folder, archive = Path(root) / "world", Path(root) / "archive"
            state, expected = self.fixture(folder)
            store = ContinuityStore(folder)
            result = store.offload_journal(archive)
            self.assertGreater(result["blocks"], 0)
            self.assertEqual(store.load(), state)
            self.assertEqual(store.journal(0, 500), expected)
            self.assertEqual(store.offload_journal(archive)["blocks"], 0)
            store.close()
            store = ContinuityStore(folder)
            self.assertEqual(store.verify_journal(), state["continuity"]["journal_hash"])
            store.close()
            pack = Path(result["pack"])
            data = pack.read_bytes()
            pack.unlink()
            with self.assertRaises(FileNotFoundError):
                ContinuityStore(folder)
            pack.write_bytes(data[:-1])
            with self.assertRaisesRegex(ValueError, "Truncated"):
                ContinuityStore(folder)

    def test_crashes_never_release_the_only_durable_archive(self):
        code = """
import os,sys
from subject01.continuity import ContinuityStore
s=ContinuityStore(sys.argv[1])
s.fault_hook=lambda phase: os._exit(77) if phase==sys.argv[3] else None
s.offload_journal(sys.argv[2])
"""
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            state, expected = self.fixture(root / "base")
            for phase in ("after_pack_sync", "after_pack_references", "after_pack_release", "before_commit", "after_commit"):
                with self.subTest(phase=phase):
                    folder = root / phase
                    folder.mkdir()
                    shutil.copyfile(root / "base" / "continuity.sqlite3", folder / "continuity.sqlite3")
                    process = subprocess.run([sys.executable, "-c", code, str(folder), str(folder / "archive"), phase], capture_output=True, timeout=30)
                    self.assertEqual(process.returncode, 77, process.stderr.decode())
                    store = ContinuityStore(folder)
                    try:
                        self.assertEqual(store.load(), state)
                        self.assertEqual(store.journal(0, 500), expected)
                        self.assertEqual(store.verify_journal(), state["continuity"]["journal_hash"])
                    finally:
                        store.close()


if __name__ == "__main__":
    unittest.main()
