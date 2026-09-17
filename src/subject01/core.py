from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any

SCHEMA_VERSION = 1
ARCHITECTURE_ID = "subject01-core-v0.1"
MASK_64 = (1 << 64) - 1


@dataclass(frozen=True)
class SimulationConfig:
    seed: int = 1
    dt: float = 0.05
    width: float = 100.0
    height: float = 60.0
    gravity_y: float = 0.0
    snapshot_interval_ticks: int = 200


class DeterministicRng:
    """Small serializable PRNG. Not suitable for cryptography."""

    def __init__(self, seed: int) -> None:
        self.state = seed & MASK_64 or 0x9E3779B97F4A7C15

    def next_u64(self) -> int:
        x = self.state
        x ^= (x << 13) & MASK_64
        x ^= x >> 7
        x ^= (x << 17) & MASK_64
        self.state = x & MASK_64
        return self.state

    def uniform(self, low: float, high: float) -> float:
        unit = self.next_u64() / MASK_64
        return low + (high - low) * unit


@dataclass
class WorldObject:
    object_id: str
    kind: str
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    radius: float = 1.0
    mass: float = 1.0


@dataclass
class Intervention:
    event_id: int
    kind: str
    payload: dict[str, Any]
    submitted_at_tick: int


class SimulationCore:
    """Pure deterministic state machine. It contains no wall-clock or I/O."""

    ALLOWED_COMMANDS = {"spawn_object", "impulse", "remove_object", "set_gravity"}

    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()
        if not all(math.isfinite(v) for v in (
            self.config.dt, self.config.width, self.config.height, self.config.gravity_y
        )):
            raise ValueError("configuration must be finite")
        if self.config.dt <= 0:
            raise ValueError("dt must be positive")
        if self.config.width <= 0 or self.config.height <= 0:
            raise ValueError("world dimensions must be positive")
        self.tick_index = 0
        self.next_event_id = 1
        self.gravity_y = self.config.gravity_y
        self.rng = DeterministicRng(self.config.seed)
        self.objects: dict[str, WorldObject] = {}
        self.pending: list[Intervention] = []

    @property
    def simulation_time(self) -> float:
        return self.tick_index * self.config.dt

    def submit(self, kind: str, payload: dict[str, Any]) -> Intervention:
        if kind not in self.ALLOWED_COMMANDS:
            raise ValueError(f"unsupported intervention: {kind}")
        normalized = self._validate(kind, payload)
        if any(isinstance(value, (int, float)) and not math.isfinite(value)
               for value in normalized.values()):
            raise ValueError("intervention numbers must be finite")
        if kind == "spawn_object" and (
            normalized["radius"] * 2 > min(self.config.width, self.config.height)
        ):
            raise ValueError("object is larger than the world")
        command = Intervention(
            event_id=self.next_event_id,
            kind=kind,
            payload=normalized,
            submitted_at_tick=self.tick_index,
        )
        self.next_event_id += 1
        self.pending.append(command)
        return command

    def step(self) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        commands, self.pending = sorted(self.pending, key=lambda item: item.event_id), []
        for command in commands:
            result = self._apply(command)
            applied.append(
                {
                    "event_id": command.event_id,
                    "kind": command.kind,
                    "payload": command.payload,
                    "applied_at_tick": self.tick_index,
                    "result": result,
                }
            )
        self._integrate()
        self.tick_index += 1
        return applied

    def _validate(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        if kind == "spawn_object":
            x = float(payload["x"])
            y = float(payload["y"])
            radius = float(payload.get("radius", 1.0))
            mass = float(payload.get("mass", 1.0))
            if not (0 < radius and 0 < mass):
                raise ValueError("radius and mass must be positive")
            return {
                "kind": str(payload.get("kind", "unknown")),
                "x": x,
                "y": y,
                "vx": float(payload.get("vx", 0.0)),
                "vy": float(payload.get("vy", 0.0)),
                "radius": radius,
                "mass": mass,
            }
        if kind == "impulse":
            return {
                "object_id": str(payload["object_id"]),
                "ix": float(payload["ix"]),
                "iy": float(payload["iy"]),
            }
        if kind == "remove_object":
            return {"object_id": str(payload["object_id"])}
        return {"gravity_y": float(payload["gravity_y"])}

    def _apply(self, command: Intervention) -> dict[str, Any]:
        p = command.payload
        if command.kind == "spawn_object":
            object_id = f"obj-{command.event_id}"
            self.objects[object_id] = WorldObject(object_id=object_id, **p)
            return {"ok": True, "object_id": object_id}
        if command.kind == "set_gravity":
            self.gravity_y = p["gravity_y"]
            return {"ok": True}
        target = self.objects.get(p["object_id"])
        if target is None:
            return {"ok": False, "error": "object_not_found"}
        if command.kind == "remove_object":
            del self.objects[target.object_id]
            return {"ok": True}
        target.vx += p["ix"] / target.mass
        target.vy += p["iy"] / target.mass
        return {"ok": True}

    def _integrate(self) -> None:
        dt = self.config.dt
        for object_id in sorted(self.objects):
            obj = self.objects[object_id]
            obj.vy += self.gravity_y * dt
            obj.x += obj.vx * dt
            obj.y += obj.vy * dt
            self._contain(obj)

    def _contain(self, obj: WorldObject) -> None:
        min_x, max_x = obj.radius, self.config.width - obj.radius
        min_y, max_y = obj.radius, self.config.height - obj.radius
        if min_x > max_x or min_y > max_y:
            raise ValueError("object is larger than the world")
        if obj.x < min_x:
            obj.x, obj.vx = min_x, abs(obj.vx)
        elif obj.x > max_x:
            obj.x, obj.vx = max_x, -abs(obj.vx)
        if obj.y < min_y:
            obj.y, obj.vy = min_y, abs(obj.vy)
        elif obj.y > max_y:
            obj.y, obj.vy = max_y, -abs(obj.vy)
        if not all(math.isfinite(v) for v in (obj.x, obj.y, obj.vx, obj.vy)):
            raise ValueError("non-finite physics state")

    def state(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "architecture_id": ARCHITECTURE_ID,
            "config": asdict(self.config),
            "tick_index": self.tick_index,
            "next_event_id": self.next_event_id,
            "gravity_y": self.gravity_y,
            "rng_state": self.rng.state,
            "objects": [asdict(self.objects[key]) for key in sorted(self.objects)],
            "pending": [asdict(item) for item in sorted(self.pending, key=lambda x: x.event_id)],
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "SimulationCore":
        if state.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported snapshot schema")
        core = cls(SimulationConfig(**state["config"]))
        core.tick_index = int(state["tick_index"])
        core.next_event_id = int(state["next_event_id"])
        core.gravity_y = float(state["gravity_y"])
        core.rng.state = int(state["rng_state"])
        core.objects = {
            item["object_id"]: WorldObject(**item) for item in state.get("objects", [])
        }
        core.pending = [Intervention(**item) for item in state.get("pending", [])]
        return core

    def state_hash(self) -> str:
        encoded = json.dumps(
            self.state(), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def birth_manifest(self) -> dict[str, Any]:
        architecture_hash = hashlib.sha256(ARCHITECTURE_ID.encode()).hexdigest()
        return {
            "status": "PRE-BIRTH",
            "schema_version": SCHEMA_VERSION,
            "architecture_id": ARCHITECTURE_ID,
            "architecture_hash": architecture_hash,
            "seed": self.config.seed,
            "initial_state_hash": self.state_hash(),
            "sensor_channels": [],
            "motor_channels": [],
            "language_present": False,
            "scalar_reward_present": False,
        }
