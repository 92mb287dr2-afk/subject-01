import json
import math
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from subject01.core import SimulationCore, SimulationConfig
from subject01.organism import ObserverCore, PredictiveNetwork
from subject01.observer import ObserverRuntime, ObserverServer


def test_predictive_learning_and_structural_events():
    brain = PredictiveNetwork(123)
    sensors = (0.1, 0.4, 0.0, 0.8, 0.3, 0.5, 0.9, 0.1)
    events = []
    for tick in range(401):
        _, current = brain.step(sensors, tick)
        events.extend(current)
        if tick == 1:
            initial_error = brain.error
    assert brain.error < initial_error
    assert any(e["kind"] == "created" for e in events)
    assert any(e["kind"] == "weight" for e in events)
    assert any(e["kind"] == "signal" for e in events)
    assert all(math.isfinite(e["weight"]) for e in brain.edges)


def test_network_has_only_numeric_sensor_interface():
    brain = PredictiveNetwork(1)
    with pytest.raises(ValueError):
        brain.step((0.0,) * 7, 1)
    with pytest.raises(ValueError):
        brain.step((float("nan"),) * 8, 1)


def test_body_follows_neural_motor_output():
    core = ObserverCore()
    before = dict(core.body)
    for _ in range(10):
        core.step()
    assert (core.body["x"], core.body["y"]) != (before["x"], before["y"])
    assert len(core.brain.nodes) == 32
    assert all(0 <= v <= 1 for v in core.sense())


def test_actual_signal_matches_source_activation_times_weight():
    core = ObserverCore()
    core.step()
    values = {n["id"]: n["value"] for n in core.brain.nodes}
    edges = {e["id"]: e for e in core.brain.edges}
    for event in core.last_neural_events:
        if event["kind"] == "signal":
            edge = edges[event["edge"]]
            assert event["value"] == values[edge["source"]] * edge["weight"]


def test_deterministic_observer_roundtrip():
    core = ObserverCore(SimulationConfig(seed=19))
    for _ in range(83):
        core.step()
    restored = ObserverCore.from_state(json.loads(json.dumps(core.state())))
    for _ in range(40):
        assert core.step() == restored.step()
        assert core.state_hash() == restored.state_hash()
        assert core.last_neural_events == restored.last_neural_events
    exposed = core.state()
    exposed["observer_model"]["brain"]["edges"][0]["weight"] = 999
    assert core.brain.edges[0]["weight"] != 999


def test_old_prebirth_saves_are_not_silently_repurposed():
    with pytest.raises(ValueError):
        ObserverCore.from_state(SimulationCore().state())


@pytest.mark.parametrize("kind,payload", [
    ("set_gravity", {"gravity_y": float("nan")}),
    ("spawn_object", {"x": float("inf"), "y": 2}),
    ("spawn_object", {"x": 2, "y": 2, "radius": 1000}),
    ("impulse", {"object_id": "x", "ix": 0, "iy": float("-inf")}),
])
def test_invalid_interventions_never_enter_queue(kind, payload):
    core = SimulationCore()
    with pytest.raises(ValueError):
        core.submit(kind, payload)
    assert core.pending == []
    assert core.next_event_id == 1


def test_running_without_clients_and_recovery(tmp_path):
    runtime = ObserverRuntime.open(tmp_path)
    runtime.start()
    try:
        time.sleep(.22)
        tick = runtime.status()["tick_index"]
        assert 2 <= tick < 12, "world timer must be paced, not busy-spinning"
        assert runtime.telemetry()["running"]
    finally:
        runtime.stop()
    saved = runtime.status()
    reopened = ObserverRuntime.open(tmp_path)
    assert reopened.status() == saved
    rows = [json.loads(line) for line in (tmp_path / "neural-events.jsonl").read_text().splitlines()]
    seqs = [e["seq"] for row in rows for e in row["events"]]
    assert seqs == list(range(1, runtime.core.neural_sequence + 1))


def test_backlog_has_explicit_gap_and_complete_recent_batches(tmp_path):
    runtime = ObserverRuntime.open(tmp_path)
    for _ in range(245):
        applied = runtime.core.step()
        runtime._after_step(applied)
    data = runtime.telemetry(0)
    assert data["gap"]
    assert len(data["batches"]) == 40
    data = runtime.telemetry(243)
    assert [b["tick"] for b in data["batches"]] == [244, 245]
    data["snapshot"]["body"]["x"] = -100
    assert runtime.telemetry()["snapshot"]["body"]["x"] >= 1


@pytest.fixture
def live_server(tmp_path):
    runtime = ObserverRuntime.open(tmp_path)
    server = ObserverServer(runtime, 0)
    runtime.start()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05})
    thread.start()
    try:
        yield runtime, server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        runtime.stop()


def test_http_assets_and_write_protection(live_server):
    runtime, server, url = live_server
    for path in ("/", "/app.js", "/style.css"):
        with urlopen(url + path) as response:
            assert response.status == 200
            assert len(response.read()) > 500
    payload = json.dumps({"kind": "spawn_object", "payload": {"x": 30, "y": 25}}).encode()
    with pytest.raises(HTTPError) as error:
        urlopen(Request(url + "/api/command", data=payload, method="POST"))
    assert error.value.code == 403
    headers = {"Content-Type": "application/json", "X-Observer-Token": server.token}
    with urlopen(Request(url + "/api/command", data=payload, headers=headers)) as response:
        assert response.status == 202
    with pytest.raises(HTTPError) as error:
        urlopen(Request(url + "/api/command", data=payload,
                        headers={**headers, "Origin": "https://example.com"}))
    assert error.value.code == 403
    with urlopen(url + "/api/frames") as response:
        result = json.load(response)
    assert result["snapshot"]["brain"]["nodes"]
    assert result["error"] is None


def test_long_tick_sleep_is_interruptible(tmp_path):
    runtime = ObserverRuntime.open(tmp_path, SimulationConfig(dt=2))
    runtime.start()
    started = time.monotonic()
    runtime.stop()
    assert time.monotonic() - started < 1
