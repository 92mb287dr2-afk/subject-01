"""Durable pre-birth runner. This module deliberately cannot declare a formal birth."""
from copy import deepcopy
import secrets
import math
import shutil
import threading
import time
import uuid

from .continuity import ContinuityStore, code_hash, digest
from .core import SimulationConfig
from .life import LifeCore, LAWS
from .observer import ObserverRuntime


class CandidateRuntime(ObserverRuntime):
    def __init__(self, core, store, metadata):
        super().__init__(core, store)
        self.metadata = metadata
        self.confirmations = {}
        self.closed = False
        self.paused = False

    @classmethod
    def open(cls, data_dir, config=None):
        store = ContinuityStore(data_dir)
        try:
            state = store.load()
            if state:
                metadata = state["continuity"]
                if metadata["code_hash"] != code_hash():
                    raise ValueError("Code differs from the saved environment. Explicit migration is required; state untouched")
                if metadata["status"] != "PRE-BIRTH / CANDIDATE":
                    raise ValueError("This runner only accepts pre-birth candidate states")
                core = LifeCore.from_state(state)
                if not core.kernel["intact"]:
                    raise ValueError("Kernel was explicitly destroyed; automatic replacement is forbidden")
            else:
                core = LifeCore(config or SimulationConfig())
                metadata = dict(schema_version=1, origin_id=str(uuid.uuid4()),
                                subject_id="subject-01-candidate", status="PRE-BIRTH / CANDIDATE",
                                code_hash=code_hash(), laws_hash=digest(LAWS), journal_hash="0" * 64)
                for x, y in ((.2, .3), (.7, .55), (.6, .2), (.35, .7)):
                    core.submit("spawn_object", dict(x=x * core.config.width, y=y * core.config.height, kind="stone"))
                state = core.state()
                state["continuity"] = metadata
                store.commit(state, [dict(kind="candidate_initialized", source="environment", laws=LAWS)])
            return cls(core, store, deepcopy(metadata))
        except BaseException:
            store.close()
            raise

    def _state(self, core=None, copy_memory=True):
        state = (core or self.core).state(copy_memory=copy_memory)
        state["continuity"] = deepcopy(self.metadata)
        return state

    def status(self):
        with self._lock:
            return self._state()

    def _commit(self, core, events, command=None):
        # Keep the previous confirmed state in memory until SQLite acknowledges commit.
        state = self._state(core, copy_memory=False)
        try:
            self.store.commit(state, events, command)
        except BaseException as exc:
            # Even an ambiguous post-COMMIT failure stops execution, never retries a tick.
            self.fatal_error = f"Continuity write failed: {exc}. Stopped at last confirmed state; restart to recover."
            self._running.clear()
            self._stop_requested.set()
            raise
        self.metadata = state["continuity"]
        self.core = core

    def submit(self, kind, payload, request_id=None):
        request_id = request_id or str(uuid.uuid4())
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError("Request ID must be 1..100 characters")
        with self._lock:
            request = dict(kind=kind, payload=payload)
            receipt = self.store.receipt(request_id, request)
            if receipt:
                return receipt
            if len(self.core.pending) >= 128:
                raise ValueError("Intervention queue full")
            if kind == "spawn_object" and len(self.core.objects) + len(self.core.pending) >= 256:
                raise ValueError("World object budget reached (256)")
            core = self.core.clone_for_step()
            command = core.submit(kind, payload)
            receipt = dict(event_id=command.event_id, request_id=request_id,
                           submitted_at_tick=command.submitted_at_tick, kind=kind, payload=command.payload)
            self._commit(core, [dict(kind="intervention_queued", source="observer", receipt=receipt)],
                         (request_id, request, receipt))
            return receipt

    def advance(self):
        with self._lock:
            if shutil.disk_usage(self.store.data_dir).free < 64 * 1024 * 1024:
                raise OSError("Less than 64 MiB free: technical pause; protected memories were not deleted")
            core = self.core.clone_for_step()
            core.step()
            events = deepcopy(core.last_neural_events)
            events.append(dict(kind="continuity_committed", source="store", tick=core.tick_index))
            self._commit(core, events)
            self.frames.append(self._frame())

    def _frame(self):
        frame = super()._frame()
        core = self.core
        frame.update(continuity=deepcopy(self.metadata),
                     model_graphs=dict(controller=self._model_graph(core.controller.model, "c"),
                         researcher=self._model_graph(core.researcher.model, "r")),
                     development=dict(mode=core.body["mode"], health=core.body["health"],
                         material=core.body["material"], strength=core.body["strength"],
                         memories=len(core.memories), controller_error=core.controller.error,
                         researcher_error=core.researcher.error,
                         method=core.researcher.model.selected,
                         channel_methods=core.researcher.model.channel_methods[:],
                         hypothesis=deepcopy(core.researcher.hypothesis),
                         repair=deepcopy(core.repair), resource_totals=deepcopy(core.resource_totals)))
        return frame

    @staticmethod
    def _model_graph(model, prefix):
        trace = model.last_trace
        features = trace["features"] if trace else [0.0] * 21
        weights = trace["weights"] if trace else (model.weights if hasattr(model, "weights") else model.models[1].weights)
        result = trace["result"] if trace else [0.0] * 16
        nodes = [dict(id=f"{prefix}i{i}", group="sensor" if i < 16 else "motor" if i < 20 else "hidden",
                      value=value) for i, value in enumerate(features)]
        nodes += [dict(id=f"{prefix}p{i}", group="prediction", value=value) for i, value in enumerate(result)]
        edges = [dict(id=f"{prefix}i{j}:{prefix}p{i}", source=f"{prefix}i{j}", target=f"{prefix}p{i}",
                      weight=weight, signal=weight * features[j], plastic=True, created_tick=0)
                 for i, row in enumerate(weights) for j, weight in enumerate(row)]
        return dict(nodes=nodes, edges=edges, error=model.error, inference=True)

    def start(self):
        if self.closed:
            raise RuntimeError("Runtime is closed")
        if self._running.is_set():
            return
        if not self.core.kernel["intact"]:
            raise ValueError("Destroyed kernel cannot restart")
        self._stop_requested.clear()
        self.fatal_error = None
        self._running.set()
        self._thread = threading.Thread(target=self._guarded_run, name="subject01-candidate", daemon=False)
        self._thread.start()

    def _run(self):
        deadline = time.monotonic()
        while self._running.is_set():
            deadline += self.core.config.dt
            with self._lock:
                if not self.paused:
                    self.advance()
            self._stop_requested.wait(max(0, deadline - time.monotonic()))
            if deadline < time.monotonic() - self.core.config.dt:
                deadline = time.monotonic()  # no offline catch-up

    def set_paused(self, paused):
        with self._lock:
            self._commit(self.core, [dict(kind="observer_paused" if paused else "observer_resumed",
                                         source="observer", tick=self.core.tick_index)])
            self.paused = paused
            return dict(paused=paused)

    def telemetry(self, after=-1):
        with self._lock:
            result = super().telemetry(after)
            result["paused"] = self.paused
            return result

    def save_snapshot(self):
        with self._lock:
            # Each command/tick is already durable. Do not emit an unconfirmed snapshot.
            state = self.store.load()
            if state != self._state():
                raise ValueError("In-memory state differs from the confirmed checkpoint; restart required")

    def stop(self):
        if self.closed:
            return
        self._running.clear()
        self._stop_requested.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                raise RuntimeError("Writer has not stopped; lock retained")
        with self._lock:
            self.store.close()
            self.closed = True

    def _target(self, operation, target):
        if operation == "destroy_kernel" and target == "kernel":
            return deepcopy(self.core.kernel)
        if operation in ("delete_memory", "replace_memory") and type(target) is int:
            record = next((m for m in self.core.memories if m["memory_id"] == target), None)
            if record:
                return deepcopy(record)
        if operation == "edit_recovery_policy" and target == "kernel":
            return deepcopy(self.core.kernel["policy"])
        if operation == "edit_model_weights":
            if target == "controller":
                return deepcopy(self.core.controller.model.weights)
            if target in ("researcher-0", "researcher-1", "researcher-2"):
                return deepcopy(self.core.researcher.model.models[int(target[-1])].weights)
        raise ValueError("Unknown protected operation or target")

    def _replacement(self, operation, replacement):
        def finite(value, low, high):
            return type(value) in (int, float) and math.isfinite(value) and low <= value <= high
        if operation == "replace_memory":
            if not isinstance(replacement, dict) or set(replacement) != {"sensors", "action", "tick"}:
                raise ValueError("Memory replacement requires sensors, action, tick")
            if (not isinstance(replacement["sensors"], list) or len(replacement["sensors"]) != 16
                or not all(finite(v, 0, 1) for v in replacement["sensors"])
                or not isinstance(replacement["action"], list) or len(replacement["action"]) != 4
                or not all(finite(v, -1, 1) for v in replacement["action"])
                or type(replacement["tick"]) is not int or not 0 <= replacement["tick"] <= self.core.tick_index):
                raise ValueError("Invalid numeric memory representation")
        elif operation == "edit_recovery_policy":
            bounds = dict(energy_flow=(.05, 2), material_flow=(.001, 1),
                          health_threshold=(.3, 1), energy_threshold=(.2, 1))
            if not isinstance(replacement, dict) or set(replacement) != set(bounds) or not all(
                    finite(replacement[key], *bounds[key]) for key in bounds):
                raise ValueError("Recovery policy requires finite supported resource flows and thresholds")
        elif operation == "edit_model_weights":
            if (not isinstance(replacement, list) or len(replacement) != 16 or not all(
                    isinstance(row, list) and len(row) == 21 and all(finite(v, -2, 2) for v in row)
                    for row in replacement)):
                raise ValueError("Model weights must be a finite 16 x 21 matrix in [-2,2]")
        elif replacement is not None:
            raise ValueError("This operation does not accept replacement content")
        return deepcopy(replacement)

    def preview_override(self, operation, target, replacement=None):
        with self._lock:
            current = self._target(operation, target)
            replacement = self._replacement(operation, replacement)
            token = secrets.token_urlsafe(32)
            self.confirmations = {k: v for k, v in self.confirmations.items() if v["expires"] > time.monotonic()}
            if len(self.confirmations) >= 16:
                raise ValueError("Too many outstanding confirmations")
            self.confirmations[token] = dict(operation=operation, target=target,
                checksum=digest(current), expires=time.monotonic() + 60,
                origin_id=self.metadata["origin_id"], replacement=replacement)
            self._commit(self.core, [dict(kind="observer_override_previewed", source="observer",
                                         operation=operation, target=target, prior_hash=digest(current),
                                         replacement=replacement)])
            consequence = {
                "destroy_kernel": "Ядро будет уничтожено. Возобновление и автоматическая замена запрещены.",
                "delete_memory": "Указанное защищённое воспоминание будет удалено без восстановления.",
                "replace_memory": "Содержание указанного воспоминания будет заменено внешним вмешательством.",
                "edit_recovery_policy": "Будут изменены защищённые правила восстановления и расход ресурсов.",
                "edit_model_weights": "Усвоенная модель будет изменена извне; это не результат самостоятельного обучения."
            }[operation]
            return dict(token=token, operation=operation, target=target, expires_in_seconds=60,
                        target_hash=digest(current), replacement=replacement, consequence=consequence,
                        required_confirmation="ПОДТВЕРЖДАЮ " + operation + " " + str(target))

    def confirm_override(self, token, confirmation):
        with self._lock:
            grant = self.confirmations.pop(token, None)
            if not grant or time.monotonic() > grant["expires"]:
                raise ValueError("Missing, expired, or consumed confirmation")
            operation, target = grant["operation"], grant["target"]
            expected = "ПОДТВЕРЖДАЮ " + operation + " " + str(target)
            if confirmation != expected or digest(self._target(operation, target)) != grant["checksum"]:
                raise ValueError("Confirmation does not match the exact target state")
            if grant["origin_id"] != self.metadata["origin_id"]:
                raise ValueError("Confirmation belongs to another origin")
            core = LifeCore.from_state(self.core.state())
            if operation == "destroy_kernel":
                core.kernel["intact"] = False
            elif operation == "delete_memory":
                core.memories = [m for m in core.memories if m["memory_id"] != target]
            elif operation == "replace_memory":
                memory = next(m for m in core.memories if m["memory_id"] == target)
                memory["representation"] = deepcopy(grant["replacement"])
                memory["integrity_hash"] = digest({k: v for k, v in memory.items() if k != "integrity_hash"})
            elif operation == "edit_recovery_policy":
                core.kernel["policy"] = deepcopy(grant["replacement"])
            else:
                model = (core.controller.model if target == "controller" else
                         core.researcher.model.models[int(target[-1])])
                model.weights = deepcopy(grant["replacement"])
                model.last_trace = None
            audit = dict(kind="observer_override_confirmed", source="observer", operation=operation,
                         target=target, prior_hash=grant["checksum"], tick=core.tick_index,
                         replacement=grant["replacement"])
            self._commit(core, [audit])
            self.frames.clear()
            if operation == "destroy_kernel":
                self.fatal_error = "Kernel explicitly destroyed. Automatic replacement is forbidden."
                self._running.clear()
                self._stop_requested.set()
            return dict(applied=True, operation=operation, target=target)
