"""Diagnostic embodied network, NOT the formal birth of Subject-01.

The brain accepts eight numeric channels, never world objects or observer commands.
Sparse prediction readouts learn the next sensory vector via online delta learning.
Motor readouts are fixed: adaptive locomotion or consciousness are NOT claimed.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .core import DeterministicRng, SimulationConfig, SimulationCore

MODEL = "sensorimotor-diagnostic-v1"


def clip(value, low=0.0, high=1.0):
    return max(low, min(high, value))


class PredictiveNetwork:
    def __init__(self, seed):
        self.rng = DeterministicRng(seed ^ 0xD1A6)
        self.nodes = (
            [{"id": f"s{i}", "group": "sensor", "value": 0.0} for i in range(8)]
            + [{"id": f"h{i}", "group": "hidden", "value": 0.0} for i in range(12)]
            + [{"id": f"m{i}", "group": "motor", "value": 0.0} for i in range(4)]
            + [{"id": f"p{i}", "group": "prediction", "value": 0.5} for i in range(8)]
        )
        self.edges = []
        for h in range(12):
            for offset in range(3):
                self.add_edge(f"s{(h + offset * 3) % 8}", f"h{h}",
                              self.rng.uniform(-1, 1), False, 0)
        for m in range(4):
            for h in range(m, 12, 4):
                self.add_edge(f"h{h}", f"m{m}", self.rng.uniform(-1, 1), False, 0)
        for p in range(8):
            self.add_edge(f"h{p}", f"p{p}", self.rng.uniform(-0.1, 0.1), True, 0)
        self.previous_hidden = None
        self.previous_prediction = None
        self.error = None
        self.steps = 0

    def add_edge(self, source, target, weight, plastic, tick):
        edge = dict(id=source + ":" + target, source=source, target=target,
                    weight=weight, plastic=plastic, created_tick=tick)
        self.edges.append(edge)
        return edge

    def step(self, sensors, tick):
        if len(sensors) != 8 or not all(math.isfinite(v) for v in sensors):
            raise ValueError("exactly eight finite numeric sensor channels required")
        events = []
        if self.previous_prediction is not None:
            errors = [sensors[i] - self.previous_prediction[i] for i in range(8)]
            self.error = sum(e * e for e in errors) / 8
            for edge in self.edges:
                if edge["plastic"]:
                    h, p = int(edge["source"][1:]), int(edge["target"][1:])
                    before = edge["weight"]
                    edge["weight"] = clip(before + 0.035 * errors[p] *
                                          self.previous_hidden[h], -2, 2)
                    if edge["weight"] != before:
                        events.append(dict(kind="weight", edge=edge["id"],
                                           before=before, weight=edge["weight"]))
            # Grow one missing prediction edge using the largest local error gradient.
            # This is an explicit learning rule, not unrestricted self-modification.
            if self.steps % 40 == 0:
                existing = {e["id"] for e in self.edges}
                candidates = [(abs(errors[p] * self.previous_hidden[h]), h, p)
                              for h in range(12) for p in range(8)
                              if f"h{h}:p{p}" not in existing]
                if candidates:
                    score, h, p = max(candidates)
                    if score > 1e-5:
                        edge = self.add_edge(f"h{h}", f"p{p}",
                                             0.035 * errors[p] * self.previous_hidden[h],
                                             True, tick)
                        events.append(dict(kind="created", edge=edge["id"],
                                           source=edge["source"], target=edge["target"],
                                           weight=edge["weight"]))
        values = {f"s{i}": clip(value) for i, value in enumerate(sensors)}

        def transmit(edge):
            signal = values[edge["source"]] * edge["weight"]
            # Every nonzero computed transmission is recorded, no visual fake pulses.
            if signal != 0:
                events.append(dict(kind="signal", edge=edge["id"], value=signal))
            return signal

        for h in range(12):
            target = f"h{h}"
            current = sum(transmit(e) for e in self.edges if e["target"] == target)
            # Explicit seeded endogenous exploration, not observer-driven animation.
            current += self.rng.uniform(-0.18, 0.18)
            values[target] = 0.5 + 0.5 * math.tanh(current)
        for m in range(4):
            target = f"m{m}"
            values[target] = math.tanh(sum(transmit(e) for e in self.edges
                                           if e["target"] == target))
        for p in range(8):
            target = f"p{p}"
            values[target] = clip(0.5 + sum(transmit(e) for e in self.edges
                                          if e["target"] == target))
        for node in self.nodes:
            node["value"] = values[node["id"]]
        self.previous_hidden = [values[f"h{i}"] for i in range(12)]
        self.previous_prediction = [values[f"p{i}"] for i in range(8)]
        self.steps += 1
        return [values[f"m{i}"] for i in range(4)], events

    def state(self):
        return deepcopy(dict(nodes=self.nodes, edges=self.edges, error=self.error,
                             previous_hidden=self.previous_hidden,
                             previous_prediction=self.previous_prediction,
                             steps=self.steps, rng_state=self.rng.state))

    @classmethod
    def restore(cls, state):
        brain = cls(1)
        for key in ("nodes", "edges", "error", "previous_hidden",
                    "previous_prediction", "steps"):
            setattr(brain, key, deepcopy(state[key]))
        brain.rng.state = state["rng_state"]
        return brain


class ObserverCore(SimulationCore):
    """Top-down, point-body diagnostic model, isolated from baseline PRE-BIRTH saves."""
    def __init__(self, config=None):
        super().__init__(config)
        self.brain = PredictiveNetwork(self.config.seed)
        self.body = dict(x=self.config.width / 2, y=self.config.height / 2,
                         vx=0.0, vy=0.0, energy=1.0, contact=0.0)
        self.neural_sequence = 0
        self.last_neural_events = []

    def sense(self):
        b, c = self.body, self.config
        channels = [clip(1 - (b["x"] - 1) / 15),
                    clip(1 - (c.width - 1 - b["x"]) / 15),
                    clip(1 - (b["y"] - 1) / 15),
                    clip(1 - (c.height - 1 - b["y"]) / 15), 0.0, 0.0,
                    b["energy"], clip(math.hypot(b["vx"], b["vy"]) / 6)]
        for obj in self.objects.values():
            dx, dy = obj.x - b["x"], obj.y - b["y"]
            proximity = clip(1 - math.hypot(dx, dy) / 20)
            index = 4 if dx < 0 else 5
            channels[index] = max(channels[index], proximity)
        return tuple(channels)

    def step(self):
        applied = super().step()
        motors, events = self.brain.step(self.sense(), self.tick_index)
        b, c = self.body, self.config
        ax, ay = (motors[0] - motors[1]) * 5, (motors[2] - motors[3]) * 5
        b["vx"] = clip((b["vx"] + ax * c.dt) * math.exp(-c.dt), -6, 6)
        b["vy"] = clip((b["vy"] + ay * c.dt) * math.exp(-c.dt), -6, 6)
        x, y = b["x"] + b["vx"] * c.dt, b["y"] + b["vy"] * c.dt
        b["x"], b["y"] = clip(x, 1, c.width - 1), clip(y, 1, c.height - 1)
        b["contact"] = float(x != b["x"] or y != b["y"])
        if x != b["x"]:
            b["vx"] = 0.0
        if y != b["y"]:
            b["vy"] = 0.0
        # Objects collide with the abstract body; this is not articulated biomechanics.
        for obj in sorted(self.objects.values(), key=lambda item: item.object_id):
            dx, dy = b["x"] - obj.x, b["y"] - obj.y
            distance = math.hypot(dx, dy)
            overlap = 1 + obj.radius - distance
            if overlap > 0:
                nx, ny = (dx / distance, dy / distance) if distance else (1.0, 0.0)
                b["x"] = clip(b["x"] + nx * overlap, 1, c.width - 1)
                b["y"] = clip(b["y"] + ny * overlap, 1, c.height - 1)
                b["vx"], b["vy"], b["contact"] = 0.0, 0.0, 1.0
        effort = sum(abs(v) for v in motors) / 4
        b["energy"] = clip(b["energy"] + c.dt * (0.005 - effort * 0.012))
        for event in events:
            self.neural_sequence += 1
            event.update(seq=self.neural_sequence, tick=self.tick_index)
        self.last_neural_events = events
        return applied

    def state(self):
        state = super().state()
        state["observer_model"] = dict(version=MODEL, body=deepcopy(self.body),
                                      brain=self.brain.state(),
                                      neural_sequence=self.neural_sequence)
        return state

    @classmethod
    def from_state(cls, state):
        if state.get("observer_model", {}).get("version") != MODEL:
            raise ValueError("not a compatible observer snapshot; use a separate data directory")
        core = super().from_state(state)
        model = state["observer_model"]
        core.body = deepcopy(model["body"])
        core.brain = PredictiveNetwork.restore(model["brain"])
        core.neural_sequence = model["neural_sequence"]
        return core

    def birth_manifest(self):
        manifest = super().birth_manifest()
        manifest.update(status="PRE-BIRTH / DIAGNOSTIC", diagnostic_model=MODEL,
                        sensor_channels=list(range(8)), motor_channels=list(range(4)))
        return manifest
