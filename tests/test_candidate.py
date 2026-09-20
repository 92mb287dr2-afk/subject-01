"""Acceptance-oriented tests runnable without third-party dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from subject01.candidate import CandidateRuntime
from subject01.continuity import ContinuityStore, digest
from subject01.core import SimulationConfig
from subject01.development import ActionModel
from subject01.life import LifeCore, LAWS


class CandidateAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.opened = []

    def tearDown(self):
        for runtime in self.opened:
            runtime.stop()
        self.temp.cleanup()

    def runtime(self, path=None):
        runtime = CandidateRuntime.open(path or self.path)
        self.opened.append(runtime)
        return runtime

    def test_C01_all_components_continue_identically_1000_ticks(self):
        a = LifeCore(SimulationConfig(seed=19))
        for _ in range(103):
            a.step()
        b = LifeCore.from_state(json.loads(json.dumps(a.state())))
        for i in range(1000):
            if i == 300:
                for core in (a, b):
                    core.submit("damage_body", {"amount": 1})
            a.step()
            b.step()
            self.assertEqual(a.state_hash(), b.state_hash())
            self.assertEqual(a.last_neural_events, b.last_neural_events)

    def test_C02_atomic_command_deduplication_after_restart(self):
        r = self.runtime()
        receipt = r.submit("damage_body", {"amount": .5}, "same-request")
        r.stop()
        r = self.runtime()
        self.assertEqual(receipt, r.submit("damage_body", {"amount": .5}, "same-request"))
        r.advance()
        health = r.core.body["health"]
        r.submit("damage_body", {"amount": .5}, "same-request")
        r.advance()
        self.assertGreaterEqual(r.core.body["health"], health)
        with self.assertRaises(ValueError):
            r.submit("damage_body", {"amount": .4}, "same-request")
        self.assertEqual(r.store.verify_journal(), r.metadata["journal_hash"])

    def test_C02_real_process_crashes_at_transaction_boundaries(self):
        for phase in ("after_begin", "after_events", "after_checkpoint", "before_commit", "after_commit"):
            with self.subTest(phase=phase):
                folder = self.path / phase
                r = self.runtime(folder)
                before = r.status()
                expected = LifeCore.from_state(r.core.state())
                expected.step()
                r.stop()
                code = """
import os,sys
from subject01.candidate import CandidateRuntime
r=CandidateRuntime.open(sys.argv[1])
r.store.fault_hook=lambda phase: os._exit(77) if phase==sys.argv[2] else None
r.advance()
"""
                process = subprocess.run([sys.executable, "-c", code, str(folder), phase],
                                         capture_output=True, timeout=30)
                self.assertEqual(process.returncode, 77, process.stderr.decode())
                reopened = self.runtime(folder)
                if phase == "after_commit":
                    self.assertEqual(reopened.core.state(), expected.state())
                else:
                    self.assertEqual(reopened.status(), before)
                reopened.store.verify_journal()
                reopened.stop()

    def test_C03_second_process_refused_then_lock_released(self):
        r = self.runtime()
        code = "from subject01.candidate import CandidateRuntime; import sys; r=CandidateRuntime.open(sys.argv[1]); r.stop()"
        result = subprocess.run([sys.executable, "-c", code, str(self.path)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"running writer", result.stderr)
        r.stop()
        result = subprocess.run([sys.executable, "-c", code, str(self.path)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_C04_restart_does_not_advance_wall_time_and_needs_no_ui(self):
        r = self.runtime()
        r.advance()
        state = r.status()
        r.stop()
        with patch("time.time", return_value=time.time() + 86400):
            r = self.runtime()
            self.assertEqual(r.status(), state)
        r.start()
        deadline = time.monotonic() + 3
        while r.core.tick_index < 4 and time.monotonic() < deadline:
            time.sleep(.02)
        r.stop()
        self.assertGreaterEqual(r.core.tick_index, 4)

    def test_C05_C06_memory_survives_damage_buffer_eviction_and_restart(self):
        r = self.runtime()
        r.advance()
        first = r.core.memories[0].copy()
        r.submit("damage_body", {"amount": 1})
        for _ in range(150):
            r.advance()
        self.assertNotIn(first["representation"], r.core.experience)
        self.assertEqual(r.core.memories[0], first)
        with self.assertRaises(ValueError):
            r.submit("delete_memory", {"memory_id": first["memory_id"]})
        r.stop()
        r = self.runtime()
        self.assertEqual(r.core.memories[0], first)

    def test_C07_revision_preserves_original_experience(self):
        core = LifeCore()
        core.step()
        memory = core.memories[0].copy()
        model = core.controller.model
        sensors, action = [.5] * 16, [.4] * 4
        for _ in range(80):
            model.learn(sensors, action, [.6] * 16)
        before = model.predict(sensors, action)
        for _ in range(80):
            model.learn(sensors, action, [.4] * 16)
        self.assertLess(model.predict(sensors, action)[0], before[0] - .1)
        self.assertEqual(core.memories[0], memory)

    def test_C08_exact_one_use_expiring_override_and_no_rebirth(self):
        r = self.runtime()
        r.advance()
        grant = r.preview_override("delete_memory", 1)
        with self.assertRaises(ValueError):
            r.confirm_override(grant["token"], "yes")
        self.assertEqual(len(r.core.memories), 1)
        grant = r.preview_override("delete_memory", 1)
        r.confirm_override(grant["token"], grant["required_confirmation"])
        with self.assertRaises(ValueError):
            r.confirm_override(grant["token"], grant["required_confirmation"])
        self.assertFalse(r.core.memories)
        r.advance()
        self.assertEqual(r.core.memories[0]["memory_id"], 2)
        grant = r.preview_override("destroy_kernel", "kernel")
        r.confirmations[grant["token"]]["expires"] = -1
        with self.assertRaises(ValueError):
            r.confirm_override(grant["token"], grant["required_confirmation"])
        grant = r.preview_override("destroy_kernel", "kernel")
        r.confirm_override(grant["token"], grant["required_confirmation"])
        r.stop()
        with self.assertRaisesRegex(ValueError, "destroyed"):
            self.runtime()

    def test_C09_hidden_labels_do_not_change_either_learner(self):
        a, b = LifeCore(), LifeCore()
        a.submit("spawn_object", dict(x=20, y=20, kind="human-readable"))
        b.submit("spawn_object", dict(x=20, y=20, kind="scrambled"))
        for _ in range(120):
            a.step()
            b.step()
        self.assertEqual(a.controller.state(), b.controller.state())
        self.assertEqual(a.researcher.state(), b.researcher.state())
        with self.assertRaises(ValueError):
            a.controller.observe(["observer"] * 16)

    def test_C11_C12_C13_numeric_boundary_body_limits_and_single_arbiter(self):
        core = LifeCore()
        for command in ("add_limb", "set_brain", "replace_kernel"):
            with self.assertRaises(ValueError):
                core.submit(command, {})
        for _ in range(170):
            core.step()
            self.assertEqual(core.body["motors"], core.arbiter.last["action"])
            self.assertEqual(core.controller.previous, core.researcher.previous)
            self.assertEqual(core.controller.action, core.researcher.action)
            self.assertGreaterEqual(core.body["strength"], LAWS["strength_min"])
            self.assertLessEqual(core.body["strength"], LAWS["strength_max"])
            self.assertEqual(len(core.body["joints"]), 4)
        self.assertIsNot(core.controller.model.weights, core.researcher.model.models[0].weights)
        self.assertFalse(hasattr(core.researcher, "body"))

    def test_C15_C16_C17_model_trial_then_divergence_and_forward_compensation(self):
        core = LifeCore()
        for _ in range(100):
            core.step()
        hypothesis = core.researcher.hypothesis
        self.assertIsNotNone(hypothesis)
        self.assertEqual(hypothesis["phase"], "model_trial")
        self.assertEqual(core.body["strength"], 1)
        # Ensure a meaningful bounded change independently of sampled delta.
        hypothesis["delta"] = .04
        for _ in range(5):
            core.step()
        self.assertGreater(core.body["strength"], 1)
        original_tick = core.tick_index
        memories = core.memories[:]
        core.researcher.hypothesis["expected_next"] = [10.0] * 16
        core.step()
        self.assertIsNotNone(core.repair)
        self.assertTrue(any(e["kind"] == "change_rejected" for e in core.last_neural_events))
        spent = core.resource_totals["material_out"]
        for _ in range(10):
            core.step()
        self.assertAlmostEqual(core.body["strength"], 1)
        self.assertGreater(core.tick_index, original_tick)
        self.assertGreater(core.resource_totals["material_out"], spent)
        self.assertEqual(core.memories[:len(memories)], memories)

    def test_C19_C20_recovery_keeps_origin_memory_and_balances_resources(self):
        core = LifeCore()
        core.step()
        memory = core.memories[0].copy()
        core.submit("damage_body", {"amount": 1})
        for _ in range(400):
            core.step()
        self.assertEqual(core.body["mode"], "ACTIVE")
        self.assertGreater(core.body["health"], .75)
        self.assertEqual(core.kernel["recoveries"], 1)
        self.assertEqual(core.memories[0], memory)
        totals = core.resource_totals
        self.assertAlmostEqual(core.body["energy"], 1 + totals["energy_in"] - totals["energy_out"] - totals["energy_spill"])
        self.assertAlmostEqual(core.body["material"], .5 + totals["material_in"] - totals["material_out"] - totals["material_spill"])

    def test_C21_failed_commit_never_publishes_an_uncommitted_frame(self):
        r = self.runtime()
        r.advance()
        before = r.status()
        cursor = r.telemetry()["cursor"]
        def fail(phase):
            if phase == "before_commit":
                raise OSError("simulated disk full")
        r.store.fault_hook = fail
        with self.assertRaises(OSError):
            r.advance()
        self.assertEqual(r.status(), before)
        self.assertEqual(r.telemetry()["cursor"], cursor)
        self.assertEqual(r.store.load(), before)

    def test_C21_action_graph_matches_recorded_inference_operands(self):
        r = self.runtime()
        r.advance()
        for name, graph in r.telemetry()["snapshot"]["model_graphs"].items():
            values = {n["id"]: n["value"] for n in graph["nodes"]}
            for edge in graph["edges"]:
                self.assertEqual(edge["signal"], edge["weight"] * values[edge["source"]], name)

    def test_structural_growth_requires_trial_and_failure_costs_forward_repair(self):
        core = LifeCore()
        for _ in range(80):
            core.step()
        p = core.brain.proposal
        self.assertIsNotNone(p)
        self.assertEqual(p["phase"], "model_trial")
        edge_id = p["source"] + ":" + p["target"]
        self.assertNotIn(edge_id, [e["id"] for e in core.brain.edges])
        material_spent = core.resource_totals["material_out"]
        core.step()
        self.assertIn(edge_id, [e["id"] for e in core.brain.edges])
        self.assertGreater(core.resource_totals["material_out"], material_spent)
        tick = core.tick_index
        # Force a physically observable prediction failure, not a mock UI event.
        core.brain.previous_prediction = [-5.0] * 8
        core.step()
        self.assertEqual(core.brain.proposal["phase"], "repairing")
        spent = core.resource_totals["material_out"]
        for _ in range(LAWS["edge_repair_ticks"]):
            core.step()
        self.assertGreater(core.tick_index, tick)
        self.assertGreater(core.resource_totals["material_out"], spent)
        self.assertNotIn(edge_id, [e["id"] for e in core.brain.edges])

    def test_numeric_channel_permutation_does_not_supply_a_body_dictionary(self):
        order = list(reversed(range(16)))
        a, b = ActionModel(), ActionModel()
        sensors, action = [.1 + i * .04 for i in range(16)], [.2, -.4, .6, -.1]
        following = [min(1, v + action[i % 4] * .03) for i, v in enumerate(sensors)]
        for _ in range(100):
            a.learn(sensors, action, following)
            b.learn([sensors[i] for i in order], action, [following[i] for i in order])
        prediction = a.predict(sensors, action)
        permuted = b.predict([sensors[i] for i in order], action)
        for i in range(16):
            self.assertAlmostEqual(permuted[i], prediction[order[i]], places=12)

    def test_protected_edits_are_exact_audited_and_persisted(self):
        r = self.runtime()
        r.advance()
        replacement = dict(sensors=[.25] * 16, action=[0.0] * 4, tick=1)
        grant = r.preview_override("replace_memory", 1, replacement)
        self.assertNotEqual(r.core.memories[0]["representation"], replacement)
        r.confirm_override(grant["token"], grant["required_confirmation"])
        self.assertEqual(r.core.memories[0]["representation"], replacement)
        policy = dict(energy_flow=.3, material_flow=.02, health_threshold=.8, energy_threshold=.7)
        grant = r.preview_override("edit_recovery_policy", "kernel", policy)
        r.confirm_override(grant["token"], grant["required_confirmation"])
        weights = [[.1] * 21 for _ in range(16)]
        grant = r.preview_override("edit_model_weights", "controller", weights)
        r.confirm_override(grant["token"], grant["required_confirmation"])
        self.assertEqual(r.core.controller.model.weights, weights)
        with self.assertRaises(ValueError):
            r.preview_override("edit_model_weights", "controller", [[float("nan")] * 21] * 16)
        with self.assertRaises(ValueError):
            r.preview_override("edit_recovery_policy", "kernel", {**policy, "energy_flow": 0})
        r.stop()
        r = self.runtime()
        self.assertEqual(r.core.kernel["policy"], policy)
        self.assertEqual(r.core.controller.model.weights, weights)
        self.assertEqual(r.core.memories[0]["representation"], replacement)
        events = [e for batch in r.store.journal() for e in batch["events"]]
        audit = [e for e in events if e["kind"] == "observer_override_confirmed"]
        self.assertEqual(len(audit), 3)
        self.assertEqual(audit[-1]["replacement"], weights)

    def test_paused_edit_and_changed_target_rejection(self):
        r = self.runtime()
        r.start()
        r.set_paused(True)
        tick = r.core.tick_index
        time.sleep(.12)
        self.assertEqual(r.core.tick_index, tick)
        self.assertTrue(r.telemetry()["paused"])
        weights = [[.1] * 21 for _ in range(16)]
        first = r.preview_override("edit_model_weights", "controller", weights)
        second = r.preview_override("edit_model_weights", "controller", [[.2] * 21 for _ in range(16)])
        r.confirm_override(second["token"], second["required_confirmation"])
        with self.assertRaisesRegex(ValueError, "exact target"):
            r.confirm_override(first["token"], first["required_confirmation"])
        r.set_paused(False)
        deadline = time.monotonic() + 2
        while r.core.tick_index == tick and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertGreater(r.core.tick_index, tick)

    def test_model_journal_reconstructs_each_inference_and_weight_exactly(self):
        from subject01.audit import ModelAudit
        r = self.runtime()
        for _ in range(8):
            r.advance()
        grant = r.preview_override("edit_model_weights", "controller", [[.1] * 21 for _ in range(16)])
        r.confirm_override(grant["token"], grant["required_confirmation"])
        for _ in range(8):
            r.advance()
        audit = ModelAudit().check_store(r.store)
        self.assertGreater(audit.inferences, 80)
        self.assertEqual(audit.models["controller"]["weights"], r.core.controller.model.weights)
        for i, model in enumerate(r.core.researcher.model.models):
            self.assertEqual(audit.models[f"researcher-{i}"]["weights"], model.weights)

    def test_consolidated_memory_is_recalled_as_a_real_action_candidate(self):
        core = LifeCore()
        core.step()
        core.step()
        events = [e for e in core.last_neural_events if e["kind"] == "memory_recalled"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["memory_id"], core.memories[0]["memory_id"])
        self.assertEqual(events[0]["action_candidate"], core.memories[0]["representation"]["action"])

    def test_no_silent_legacy_migration_corruption_or_code_change(self):
        (self.path / "latest.snapshot.json").write_text("{}")
        with self.assertRaises(ValueError):
            self.runtime()
        (self.path / "latest.snapshot.json").unlink()
        r = self.runtime()
        r.stop()
        with patch("subject01.candidate.code_hash", return_value="different"):
            with self.assertRaisesRegex(ValueError, "Code differs"):
                self.runtime()
        store = ContinuityStore(self.path)
        store.db.execute("UPDATE checkpoint SET hash='corrupted'")
        store.close()
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.runtime()


if __name__ == "__main__":
    unittest.main()
