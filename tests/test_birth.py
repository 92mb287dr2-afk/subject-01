import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from subject01 import birth
from subject01.candidate import CandidateRuntime
from subject01.continuity import code_hash, digest
from subject01.life import LAWS

validate_release = birth.release_manifest


class BirthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.report = dict(ready=True, code_hash=code_hash(), laws_hash=digest(LAWS),
                           gates={f"C{i:02}": True for i in range(1, 22)})
        self.authority = patch.object(birth, "authority_directory", return_value=self.root / "authority")
        self.release = patch.object(birth, "release_manifest", return_value=self.report)
        self.authority.start()
        self.release.start()

    def tearDown(self):
        self.release.stop()
        self.authority.stop()
        self.temp.cleanup()

    def test_one_explicit_birth_resume_and_no_replacement_after_destruction(self):
        target = self.root / "life"
        plan = birth.prepare(target)
        self.assertFalse(target.exists())
        with self.assertRaises(ValueError):
            birth.confirm("yes")
        birth.confirm(plan["confirmation"])
        runtime = birth.SubjectRuntime.open()
        runtime.advance()
        before = runtime.status()
        runtime.stop()
        birth.confirm(plan["confirmation"])
        runtime = birth.SubjectRuntime.open()
        self.assertEqual(runtime.status(), before)
        self.assertEqual(runtime.metadata["origin_id"], plan["origin_id"])
        with self.assertRaises(ValueError):
            birth.prepare(self.root / "replacement")
        grant = runtime.preview_override("destroy_kernel", "kernel")
        runtime.confirm_override(grant["token"], grant["required_confirmation"])
        runtime.stop()
        with self.assertRaisesRegex(ValueError, "destroyed"):
            birth.SubjectRuntime.open()
        birth.confirm(plan["confirmation"])
        with self.assertRaisesRegex(ValueError, "destroyed"):
            birth.SubjectRuntime.open()

    def test_diagnostics_cannot_be_renamed_into_official_life(self):
        runtime = CandidateRuntime.open(self.root / "demo")
        runtime.stop()
        with self.assertRaisesRegex(ValueError, "empty directory"):
            birth.prepare(self.root / "demo")
        self.assertIsNone(birth.read_identity())

    def test_birth_gate_rejects_unapproved_code_and_missing_acceptance(self):
        with patch.object(birth, "Path") as path:
            manifest = path.return_value.with_name.return_value
            manifest.exists.return_value = True
            for report in ({**self.report, "ready": False}, {**self.report, "code_hash": "other"},
                           {**self.report, "gates": {**self.report["gates"], "C18": False}}):
                manifest.read_text.return_value = json.dumps(report)
                with self.assertRaises(ValueError):
                    validate_release()
            manifest.read_text.return_value = json.dumps(self.report)
            self.assertEqual(validate_release(), self.report)

    def test_explicit_candidate_update_preserves_identity_and_experience(self):
        with patch("subject01.candidate.code_hash", return_value="previous-code"):
            runtime = CandidateRuntime.open(self.root / "demo")
        runtime.advance()
        before = runtime.core.state()
        origin = runtime.metadata["origin_id"]
        runtime.stop()
        with self.assertRaises(ValueError):
            birth.upgrade_candidate(self.root / "demo", "previous-code", "yes")
        birth.upgrade_candidate(self.root / "demo", "previous-code", "ОБНОВИТЬ КАНДИДАТ")
        runtime = CandidateRuntime.open(self.root / "demo")
        try:
            self.assertEqual(runtime.core.state(), before)
            self.assertEqual(runtime.metadata["origin_id"], origin)
        finally:
            runtime.stop()

    def test_crashes_resume_the_same_reserved_identity_exactly_once(self):
        script = """
import json,os,sys
from pathlib import Path
from subject01 import birth
birth.authority_directory=lambda: Path(sys.argv[1])
birth.release_manifest=lambda: json.loads(sys.argv[2])
birth.confirm(sys.argv[3], lambda phase: os._exit(77) if phase==sys.argv[4] else None)
"""
        for phase in ("after_reservation", "after_memories", "before_commit", "after_commit", "after_birth_commit"):
            with self.subTest(phase=phase), patch.object(birth, "authority_directory", return_value=self.root / phase / "authority"):
                plan = birth.prepare(self.root / phase / "life")
                result = subprocess.run([sys.executable, "-c", script, str(birth.authority_directory()),
                                         json.dumps(self.report), plan["confirmation"], phase], capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 77, result.stderr.decode())
                birth.confirm(plan["confirmation"])
                runtime = birth.SubjectRuntime.open()
                try:
                    self.assertEqual(runtime.metadata["origin_id"], plan["origin_id"])
                    self.assertEqual(runtime.core.tick_index, 0)
                    events = [e for b in runtime.store.journal() for e in b["events"]]
                    self.assertEqual(sum(e["kind"] == "subject_born" for e in events), 1)
                finally:
                    runtime.stop()


if __name__ == "__main__":
    unittest.main()
