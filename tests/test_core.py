from subject01.core import SimulationConfig, SimulationCore


def run_script(core: SimulationCore) -> None:
    core.submit("spawn_object", {"x": 10, "y": 12, "kind": "stone", "mass": 2})
    core.step()
    core.submit("impulse", {"object_id": "obj-1", "ix": 4, "iy": -2})
    core.submit("set_gravity", {"gravity_y": 3})
    for _ in range(25):
        core.step()


def test_same_seed_and_interventions_produce_same_state() -> None:
    left = SimulationCore(SimulationConfig(seed=42))
    right = SimulationCore(SimulationConfig(seed=42))
    run_script(left)
    run_script(right)
    assert left.state_hash() == right.state_hash()
    assert left.state() == right.state()


def test_intervention_waits_for_tick_boundary() -> None:
    core = SimulationCore()
    command = core.submit("spawn_object", {"x": 5, "y": 6})
    assert command.event_id == 1
    assert core.objects == {}
    applied = core.step()
    assert "obj-1" in core.objects
    assert applied[0]["event_id"] == 1
    assert applied[0]["applied_at_tick"] == 0


def test_snapshot_round_trip_preserves_everything() -> None:
    original = SimulationCore(SimulationConfig(seed=987, gravity_y=1.5))
    run_script(original)
    original.submit("spawn_object", {"x": 20, "y": 20, "kind": "pending"})
    restored = SimulationCore.from_state(original.state())
    assert restored.state() == original.state()
    assert restored.state_hash() == original.state_hash()
    original.step()
    restored.step()
    assert restored.state_hash() == original.state_hash()


def test_missing_target_is_logged_as_failed_not_crashed() -> None:
    core = SimulationCore()
    core.submit("impulse", {"object_id": "missing", "ix": 1, "iy": 1})
    applied = core.step()
    assert applied[0]["result"] == {"ok": False, "error": "object_not_found"}


def test_birth_manifest_forbids_premature_claims() -> None:
    manifest = SimulationCore(SimulationConfig(seed=11)).birth_manifest()
    assert manifest["status"] == "PRE-BIRTH"
    assert manifest["language_present"] is False
    assert manifest["scalar_reward_present"] is False
    assert manifest["sensor_channels"] == []
    assert manifest["motor_channels"] == []
