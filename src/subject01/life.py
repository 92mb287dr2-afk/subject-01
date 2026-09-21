"""Pre-birth candidate: one serializable state, limited embodied development."""
from copy import deepcopy
import math

from .continuity import digest
from .core import SimulationCore
from .development import MainController, InternalResearcher, ActionArbiter
from .organism import ObserverCore, PredictiveNetwork, clip
from .plasticity import TrialNetwork

LIFE_FORMAT = 1
LAWS = dict(dt=.05, channels=16, motors=4, joints=4, experience_window=128,
            strength_min=.6, strength_max=1.4, change_rate=.002,
            change_energy=1.0, change_material=.5, probation_ticks=40,
            mismatch_limit=.015, energy_flow=.015, recovery_energy_flow=.5,
            material_flow=.003, recovery_material_flow=.025,
            basal_cost=.005, motor_cost=.03, repair_rate=.12,
            repair_energy=.2, repair_material=.15,
            recovery_health=.75, recovery_energy=.6,
            edge_formation_energy=.003, edge_formation_material=.002,
            edge_repair_energy=.002, edge_repair_material=.001,
            edge_trial_ticks=40, edge_repair_ticks=10)


class LifeCore(ObserverCore):
    ALLOWED_COMMANDS = SimulationCore.ALLOWED_COMMANDS | {"damage_body"}

    def __init__(self, config=None):
        super().__init__(config)
        self.brain = TrialNetwork(self.config.seed)
        if self.config.dt != LAWS["dt"] or min(self.config.width, self.config.height) < 10:
            raise ValueError("Candidate laws require dt=0.05 and a world at least 10 x 10")
        self.body.update(health=1.0, material=.5, strength=1.0,
                         joints=[0.0] * 4, joint_velocity=[0.0] * 4,
                         mode="ACTIVE", motors=[0.0] * 4)
        self.controller = MainController(self.config.seed)
        self.researcher = InternalResearcher(self.config.seed)
        self.arbiter = ActionArbiter()
        self.experience = []
        self.memories = []
        self.next_memory_id = 1
        self.salience = .001
        self.kernel = dict(intact=True, recoveries=0,
                           policy=dict(energy_flow=LAWS["recovery_energy_flow"],
                                       material_flow=LAWS["recovery_material_flow"],
                                       health_threshold=LAWS["recovery_health"],
                                       energy_threshold=LAWS["recovery_energy"]))
        self.repair = None
        self.resource_totals = dict(energy_in=0.0, energy_out=0.0, energy_spill=0.0,
                                    material_in=0.0, material_out=0.0, material_spill=0.0)

    def _validate(self, kind, payload):
        if kind == "damage_body":
            if not isinstance(payload, dict):
                raise ValueError("Object required")
            amount = float(payload["amount"])
            if not math.isfinite(amount) or not 0 <= amount <= 1:
                raise ValueError("Damage must be in [0,1]")
            return dict(amount=amount)
        result = super()._validate(kind, payload)
        if any(isinstance(v, (float, int)) and (not math.isfinite(v) or abs(v) > 10000)
               for v in result.values()):
            raise ValueError("World intervention exceeds numeric limits")
        if kind == "spawn_object" and (result["mass"] < .01 or result["radius"] < .1):
            raise ValueError("Mass/radius below supported physical range")
        return result

    def _apply(self, command):
        if command.kind == "damage_body":
            self.body["health"] = max(0, self.body["health"] - command.payload["amount"])
            return dict(ok=True, health=self.body["health"])
        return super()._apply(command)

    def sensations(self):
        # The meanings here belong to the world adapter, not either learned model.
        return list(self.sense()) + [(a + 1.2) / 2.4 for a in self.body["joints"]] + [
            (self.body["vx"] + 6) / 12, (self.body["vy"] + 6) / 12,
            self.body["health"], self.body["material"]]

    def _resources(self, motors):
        b, dt = self.body, self.config.dt
        recovering = b["mode"] == "RECOVERING"
        energy_in = dt * (self.kernel["policy"]["energy_flow"] if recovering else LAWS["energy_flow"])
        material_in = dt * (self.kernel["policy"]["material_flow"] if recovering else LAWS["material_flow"])
        energy = b["energy"] + energy_in
        material = b["material"] + material_in
        basal = min(energy, dt * LAWS["basal_cost"])
        energy -= basal
        effort = min(energy, dt * LAWS["motor_cost"] * sum(v * v for v in motors))
        energy -= effort
        repair = min(1 - b["health"], dt * LAWS["repair_rate"],
                     energy / LAWS["repair_energy"], material / LAWS["repair_material"])
        energy_out = basal + effort + repair * LAWS["repair_energy"]
        material_out = repair * LAWS["repair_material"]
        energy -= repair * LAWS["repair_energy"]
        material -= material_out
        b["health"] = clip(b["health"] + repair)
        spill_e, spill_m = max(0, energy - 1), max(0, material - 1)
        b["energy"], b["material"] = clip(energy), clip(material)
        ledger = dict(energy_in=energy_in, energy_out=energy_out, energy_spill=spill_e,
                      material_in=material_in, material_out=material_out, material_spill=spill_m)
        for key, amount in ledger.items():
            self.resource_totals[key] += amount
        return dict(kind="resource_balance", source="world", **ledger, repaired=repair)

    def _develop(self, tick):
        events, b, h = [], self.body, self.researcher.hypothesis
        if self.repair:
            delta = self.repair["target"] - b["strength"]
            amount = self._change_strength(delta)
            events.append(dict(kind="repair_progress", source="development", amount=amount,
                               hypothesis_id=self.repair["id"]))
            if abs(self.repair["target"] - b["strength"]) < 1e-9:
                self.repair = None
            return events
        if not h or tick <= h["proposed_tick"]:
            return events
        h["elapsed"] += 1
        if h["phase"] == "model_trial":
            if h["uncertainty"] > LAWS["mismatch_limit"] or b["mode"] != "ACTIVE":
                events.append(dict(kind="change_rejected", source="development", reason="uncertain_model",
                                   hypothesis_id=h["id"]))
                self.researcher.hypothesis = None
                return events
            h.update(phase="probation", original=b["strength"],
                     target=clip(b["strength"] + h["delta"], LAWS["strength_min"], LAWS["strength_max"]))
            events.append(dict(kind="change_started", source="development", hypothesis_id=h["id"],
                               original=h["original"], target=h["target"], domain="real_body"))
        if h["phase"] == "probation":
            h["mismatch"] = max(h["mismatch"], self.researcher.error)
            failed = h["mismatch"] > LAWS["mismatch_limit"] or b["mode"] != "ACTIVE" or h["elapsed"] > 200
            if failed:
                self.repair = dict(id=h["id"], target=h["original"])
                events.append(dict(kind="change_rejected", source="development", hypothesis_id=h["id"],
                                   mismatch=h["mismatch"], reason="real_trial_diverged", compensation="forward_repair"))
                self.researcher.hypothesis = None
            else:
                delta = self._change_strength(h["target"] - b["strength"])
                h["age"] += 1
                if delta:
                    events.append(dict(kind="body_changed", source="development", parameter="strength",
                                       delta=delta, hypothesis_id=h["id"]))
                if h["age"] >= LAWS["probation_ticks"] and abs(b["strength"] - h["target"]) < 1e-9:
                    events.append(dict(kind="change_integrated", source="development", hypothesis_id=h["id"],
                                       mismatch=h["mismatch"], strength=b["strength"]))
                    self.researcher.hypothesis = None
        return events

    def _change_strength(self, delta):
        b = self.body
        amount = min(abs(delta), LAWS["change_rate"],
                     b["energy"] / LAWS["change_energy"], b["material"] / LAWS["change_material"])
        signed = math.copysign(amount, delta)
        b["strength"] += signed
        energy, material = amount * LAWS["change_energy"], amount * LAWS["change_material"]
        b["energy"] -= energy
        b["material"] -= material
        self.resource_totals["energy_out"] += energy
        self.resource_totals["material_out"] += material
        return signed

    def _move(self, action):
        b, dt, c = self.body, self.config.dt, self.config
        for i, torque in enumerate(action):
            speed = (b["joint_velocity"][i] + torque * b["strength"] * b["health"] * dt * 6) * math.exp(-dt * 2)
            angle = clip(b["joints"][i] + speed * dt, -1.2, 1.2)
            b["joint_velocity"][i] = 0.0 if abs(angle) == 1.2 else speed
            b["joints"][i] = angle
        # Abstract planar traction, explicitly not a biological walking simulator.
        force_x = (action[0] - action[1]) * b["strength"] * b["health"] * 5
        force_y = (action[2] - action[3]) * b["strength"] * b["health"] * 5
        b["vx"] = clip((b["vx"] + force_x * dt) * math.exp(-dt), -6, 6)
        b["vy"] = clip((b["vy"] + force_y * dt) * math.exp(-dt), -6, 6)
        x, y = b["x"] + b["vx"] * dt, b["y"] + b["vy"] * dt
        b["x"], b["y"] = clip(x, 1, c.width - 1), clip(y, 1, c.height - 1)
        b["contact"] = float(x != b["x"] or y != b["y"])
        if x != b["x"]:
            b["vx"] = 0.0
        if y != b["y"]:
            b["vy"] = 0.0
        for obj in sorted(self.objects.values(), key=lambda o: (o.x, o.y, o.radius)):
            dx, dy = b["x"] - obj.x, b["y"] - obj.y
            distance = math.hypot(dx, dy)
            overlap = 1 + obj.radius - distance
            if overlap > 0:
                nx, ny = (dx / distance, dy / distance) if distance else (1, 0)
                b["x"] = clip(b["x"] + nx * overlap, 1, c.width - 1)
                b["y"] = clip(b["y"] + ny * overlap, 1, c.height - 1)
                b["vx"], b["vy"], b["contact"] = 0.0, 0.0, 1.0
                b["health"] = max(0, b["health"] - .002)
        b["motors"] = list(action)

    def _develop_network(self, tick):
        proposal = self.brain.proposal
        if not proposal or proposal["proposed_tick"] >= tick:
            return []
        events, b = [], self.body
        proposal["elapsed"] += 1
        edge_id = proposal["source"] + ":" + proposal["target"]
        def pay(energy, material):
            if b["energy"] < energy or b["material"] < material:
                return False
            b["energy"] -= energy
            b["material"] -= material
            self.resource_totals["energy_out"] += energy
            self.resource_totals["material_out"] += material
            return True
        if proposal["phase"] == "model_trial":
            if b["mode"] != "ACTIVE" or proposal["elapsed"] > 200:
                self.brain.proposal = None
                return [dict(kind="network_change_rejected", source="development", edge=edge_id,
                             reason="resource_or_recovery_limit")]
            if not pay(LAWS["edge_formation_energy"], LAWS["edge_formation_material"]):
                return []
            edge = self.brain.add_edge(proposal["source"], proposal["target"], 0.0, False, tick)
            proposal.update(phase="probation", age=0)
            events.append(dict(kind="created", actor="development", edge=edge_id, source=edge["source"],
                               target=edge["target"], weight=0.0, trial=proposal["id"]))
        edge = next(e for e in self.brain.edges if e["id"] == edge_id)
        if proposal["phase"] == "probation":
            limit = max(.015, proposal["baseline_error"] * 2)
            if self.brain.error > limit or b["mode"] != "ACTIVE":
                proposal.update(phase="repairing", age=0)
                events.append(dict(kind="network_change_rejected", source="development", edge=edge_id,
                                   error=self.brain.error, limit=limit))
            else:
                proposal["age"] += 1
                before = edge["weight"]
                edge["weight"] = proposal["weight"] * min(1, proposal["age"] / LAWS["edge_trial_ticks"])
                events.append(dict(kind="weight", actor="development", edge=edge_id,
                                   before=before, weight=edge["weight"]))
                if proposal["age"] >= LAWS["edge_trial_ticks"]:
                    edge["plastic"] = True
                    self.brain.proposal = None
                    events.append(dict(kind="network_change_integrated", source="development", edge=edge_id))
        elif proposal["phase"] == "repairing":
            if pay(LAWS["edge_repair_energy"] / LAWS["edge_repair_ticks"],
                   LAWS["edge_repair_material"] / LAWS["edge_repair_ticks"]):
                proposal["age"] += 1
                events.append(dict(kind="network_repair_progress", source="development", edge=edge_id,
                                   age=proposal["age"]))
                if proposal["age"] >= LAWS["edge_repair_ticks"]:
                    self.brain.edges.remove(edge)
                    self.brain.proposal = None
                    events.append(dict(kind="removed", source="development", edge=edge_id))
        return events

    def step(self):
        if not self.kernel["intact"]:
            raise RuntimeError("Kernel was explicitly destroyed; replacement is forbidden")
        applied = SimulationCore.step(self)
        b, tick = self.body, self.tick_index
        events = []
        events.extend(dict(kind="intervention_applied", source="observer", intervention=item) for item in applied)
        if b["mode"] != "RECOVERING" and (b["health"] < .2 or b["energy"] < .1):
            b["mode"] = "RECOVERING"
            self.kernel["recoveries"] += 1
            events.append(dict(kind="recovery_started", source="kernel"))
        sensors = self.sensations()
        hypothesis = self.researcher.hypothesis
        if hypothesis and hypothesis.get("expected_next") is not None:
            mismatch = sum((a - b) ** 2 for a, b in zip(sensors, hypothesis["expected_next"])) / len(sensors)
            hypothesis["mismatch"] = max(hypothesis["mismatch"], mismatch)
            events.append(dict(kind="trial_observed", source="researcher", hypothesis_id=hypothesis["id"],
                               mismatch=mismatch, predicted=hypothesis["expected_next"], observed=sensors))
        self.controller.observe(sensors)
        events.extend(self.researcher.observe(sensors, tick))
        motors, neural = self.brain.step(sensors[:8], tick)
        events.extend(dict(actor="network", **e) for e in neural)
        recalled = self.controller.recall(sensors, self.memories)
        main, research = self.controller.propose(sensors, motors, recalled), self.researcher.propose()
        if recalled is not None:
            events.append(dict(kind="memory_recalled", source="controller", memory_id=self.controller.recall_id,
                               action_candidate=recalled))
        events.append(dict(kind="action_proposed", source="researcher", action=research))
        action, decision = self.arbiter.select(main, research, b["mode"] == "RECOVERING")
        events.append(decision)
        self.controller.record_action(sensors, action)
        self.researcher.record_action(sensors, action)
        if self.researcher.hypothesis:
            self.researcher.hypothesis["expected_next"] = self.researcher.model.predict(sensors, action)
        self._move(action)
        events.append(self._resources(action))
        events.extend(self._develop(tick))
        events.extend(self._develop_network(tick))
        if b["mode"] == "RECOVERING" and b["health"] >= self.kernel["policy"]["health_threshold"] and b["energy"] >= self.kernel["policy"]["energy_threshold"]:
            b["mode"] = "ACTIVE"
            events.append(dict(kind="recovery_completed", source="kernel"))
        experience = dict(tick=tick, sensors=sensors, action=action)
        self.experience = (self.experience + [experience])[-LAWS["experience_window"]:]
        error = self.controller.error
        # Selection uses only surprise/experience, not object names or observer flags.
        if not self.memories or (tick - self.memories[-1]["tick"] >= 20 and error > self.salience * 1.5):
            record = dict(memory_id=self.next_memory_id, tick=tick,
                          representation=deepcopy(experience), confidence=1 / (1 + error))
            record["integrity_hash"] = digest(record)
            self.memories.append(record)
            self.next_memory_id += 1
            events.append(dict(kind="memory_consolidated", source="controller",
                               memory_id=record["memory_id"], integrity_hash=record["integrity_hash"]))
        self.salience = .98 * self.salience + .02 * error
        if error > .01:
            events.append(dict(kind="belief_revised", source="controller", error=error))
        # Record every inference and update with its matrix version. Matrices are
        # reconstructed from zero initialization and the exact logged learning rule;
        # this avoids repeating thousands of unchanged/derivable numbers per tick.
        models = [("controller", self.controller.model)] + [
            (f"researcher-{i}", model) for i, model in enumerate(self.researcher.model.models)]
        for name, model in models:
            events.extend(dict(source=name, **event) for event in model.events)
            model.events.clear()
        for event in events:
            self.neural_sequence += 1
            event.update(seq=self.neural_sequence, tick=tick)
        self.last_neural_events = events
        return applied

    def state(self, copy_memory=True):
        state = SimulationCore.state(self)
        state["life"] = deepcopy(dict(format=LIFE_FORMAT, laws_hash=digest(LAWS), body=self.body,
            brain=self.brain.state(), controller=self.controller.state(), researcher=self.researcher.state(),
            arbiter=vars(self.arbiter), experience=self.experience,
            memories=self.memories if copy_memory else [],
            salience=self.salience, kernel=self.kernel, repair=self.repair,
            resource_totals=self.resource_totals, neural_sequence=self.neural_sequence,
            next_memory_id=self.next_memory_id))
        if not copy_memory:
            # Internal serialization view, only used under the writer lock. Existing
            # consolidated records are immutable to normal world/development steps.
            state["life"]["memories"] = self.memories
        return state

    def clone_for_step(self):
        state = self.state(copy_memory=False)
        state["life"]["memories"] = []
        core = self.from_state(state)
        # Copy the index, not every immutable record. Exceptional edits use the full
        # from_state(state()) copy instead and cannot mutate a confirmed predecessor.
        core.memories = self.memories.fork() if hasattr(self.memories, "fork") else self.memories[:]
        return core

    @classmethod
    def from_state(cls, state, lazy_memory=False):
        life = state.get("life", {})
        if life.get("format") != LIFE_FORMAT or life.get("laws_hash") != digest(LAWS):
            raise ValueError("Incompatible life state/laws; no automatic replacement")
        core = SimulationCore.from_state.__func__(cls, state)
        for name in ("body", "experience", "memories", "salience", "kernel", "repair", "resource_totals", "neural_sequence", "next_memory_id"):
            setattr(core, name, life[name] if name == "memories" and lazy_memory else deepcopy(life[name]))
        for record in (() if lazy_memory else core.memories):
            if record["integrity_hash"] != digest({k: v for k, v in record.items() if k != "integrity_hash"}):
                raise ValueError("Protected memory integrity failure")
        core.brain = TrialNetwork.restore(life["brain"])
        core.controller = MainController.restore(life["controller"])
        core.researcher = InternalResearcher.restore(life["researcher"])
        core.arbiter.__dict__.update(deepcopy(life["arbiter"]))
        return core
