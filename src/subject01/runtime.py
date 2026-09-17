from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from .core import SimulationConfig, SimulationCore


class EventStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.data_dir / "events.jsonl"
        self.snapshot_path = self.data_dir / "latest.snapshot.json"
        self.manifest_path = self.data_dir / "birth-manifest.json"
        self._write_lock = threading.Lock()

    def append(self, event_type: str, tick: int, data: dict[str, Any]) -> None:
        record = {"type": event_type, "tick": tick, "data": data}
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._write_lock, self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def save_snapshot(self, state: dict[str, Any]) -> None:
        temporary = self.snapshot_path.with_suffix(".tmp")
        encoded = json.dumps(
            state, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        with self._write_lock:
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, self.snapshot_path)

    def load_snapshot(self) -> dict[str, Any] | None:
        if not self.snapshot_path.exists():
            return None
        return json.loads(self.snapshot_path.read_text(encoding="utf-8"))

    def save_manifest_once(self, manifest: dict[str, Any]) -> None:
        if self.manifest_path.exists():
            return
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, self.manifest_path)


class SimulationRuntime:
    """Wall-clock runner around the pure core. Observer I/O never drives ticks."""

    def __init__(self, core: SimulationCore, store: EventStore) -> None:
        self.core = core
        self.store = store
        self._lock = threading.RLock()
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def open(
        cls, data_dir: str | Path, config: SimulationConfig | None = None
    ) -> "SimulationRuntime":
        store = EventStore(Path(data_dir))
        state = store.load_snapshot()
        core = SimulationCore.from_state(state) if state else SimulationCore(config)
        store.save_manifest_once(core.birth_manifest())
        return cls(core, store)

    def start(self) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._run, name="subject01-world", daemon=False
        )
        self._thread.start()
        self.store.append("runtime_started", self.core.tick_index, {})

    def stop(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("world thread did not stop")
        self.save_snapshot()
        self.store.append("runtime_stopped", self.core.tick_index, {})

    def submit(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            command = self.core.submit(kind, payload)
            record = {
                "event_id": command.event_id,
                "kind": command.kind,
                "payload": command.payload,
                "submitted_at_tick": command.submitted_at_tick,
            }
            self.store.append("intervention_queued", self.core.tick_index, record)
            return record

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self.core.state()

    def save_snapshot(self) -> None:
        with self._lock:
            state = self.core.state()
            self.store.save_snapshot(state)
            self.store.append(
                "snapshot_saved",
                self.core.tick_index,
                {"state_hash": self.core.state_hash()},
            )

    def _run(self) -> None:
        deadline = time.monotonic()
        dt = self.core.config.dt
        while self._running.is_set():
            deadline += dt
            with self._lock:
                applied = self.core.step()
                current_tick = self.core.tick_index
                for event in applied:
                    self.store.append("intervention_applied", current_tick, event)
                interval = self.core.config.snapshot_interval_ticks
                if interval > 0 and current_tick % interval == 0:
                    self.store.save_snapshot(self.core.state())
                    self.store.append(
                        "snapshot_saved",
                        current_tick,
                        {"state_hash": self.core.state_hash()},
                    )
            delay = deadline - time.monotonic()
            if delay > 0:
                self._running.wait(delay)
            else:
                deadline = time.monotonic()
