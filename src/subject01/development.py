"""Numeric-only learned models. Neither controller can access world or storage."""
from copy import deepcopy
import math

from .core import DeterministicRng
from .organism import clip

CHANNELS = 16
MOTORS = 4


def vector(values, count):
    if len(values) != count or not all(type(v) in (int, float) and math.isfinite(v) for v in values):
        raise ValueError("Finite numeric channels required")
    return tuple(float(v) for v in values)


class ActionModel:
    """Normalized delta learner; all action effects start unknown (zero weights)."""
    def __init__(self, rate=.25):
        self.rate = rate
        self.weights = [[0.0] * (CHANNELS + MOTORS + 1) for _ in range(CHANNELS)]
        self.samples = 0
        self.error = 0.0
        self.last_trace = None
        self.events = []

    def features(self, sensors, action):
        return [v - .5 for v in vector(sensors, CHANNELS)] + list(vector(action, MOTORS)) + [1.0]

    def predict(self, sensors, action):
        x = self.features(sensors, action)
        result = [clip(sensors[i] + sum(w * v for w, v in zip(row, x)))
                  for i, row in enumerate(self.weights)]
        self.last_trace = dict(features=x, weights=deepcopy(self.weights), result=result[:])
        self.events.append(dict(kind="model_inference", weights_version=self.samples,
                                features=x, sensors=list(sensors), result=result[:]))
        return result

    def learn(self, sensors, action, following):
        following = vector(following, CHANNELS)
        prediction = self.predict(sensors, action)
        x = self.features(sensors, action)
        norm = sum(v * v for v in x)
        error = sum((v - p) ** 2 for v, p in zip(following, prediction)) / CHANNELS
        for i, row in enumerate(self.weights):
            delta = self.rate * (following[i] - prediction[i]) / norm
            for j in range(len(row)):
                row[j] = clip(row[j] + delta * x[j], -2, 2)
        self.error = error
        self.samples += 1
        self.events.append(dict(kind="model_learned", weights_version=self.samples,
                                features=x, following=list(following), rate=self.rate, error=error))
        return error

    def state(self):
        return deepcopy({k: v for k, v in vars(self).items() if k != "events"})

    @classmethod
    def restore(cls, state):
        model = cls()
        model.__dict__.update(deepcopy(state))
        return model


class AdaptiveModel:
    """Bounded method adaptation by prequential errors, not privileged task labels."""
    def __init__(self, adaptive=True):
        self.models = [ActionModel(rate) for rate in (.04, .25, .9)]
        self.losses = [0.0, 0.0, 0.0]
        self.selected = 1
        self.channel_losses = [[0.0] * CHANNELS for _ in range(3)]
        self.channel_methods = [1] * CHANNELS
        self.last_trace = None
        self.error = 0.0
        self.adaptive = adaptive
        self.samples = 0

    def predict(self, sensors, action):
        predictions = [model.predict(sensors, action) for model in self.models]
        result = [predictions[self.channel_methods[i]][i] for i in range(CHANNELS)]
        self._trace(result)
        return result

    def _trace(self, result):
        self.last_trace = dict(features=self.models[0].last_trace["features"][:],
            weights=[self.models[self.channel_methods[i]].last_trace["weights"][i][:] for i in range(CHANNELS)],
            result=result[:], methods=self.channel_methods[:])

    def learn(self, sensors, action, following):
        errors = [model.learn(sensors, action, following) for model in self.models]
        channel_errors = [[(following[i] - model.last_trace["result"][i]) ** 2
                           for i in range(CHANNELS)] for model in self.models]
        observed_error = sum(channel_errors[self.channel_methods[i]][i] for i in range(CHANNELS)) / CHANNELS
        self._trace([self.models[self.channel_methods[i]].last_trace["result"][i] for i in range(CHANNELS)])
        self.error = observed_error
        self.losses = [.96 * old + .04 * new for old, new in zip(self.losses, errors)]
        self.channel_losses = [[.96 * old + .04 * new for old, new in zip(row, current)]
                               for row, current in zip(self.channel_losses, channel_errors)]
        self.samples += 1
        old = self.channel_methods[:]
        if self.adaptive:
            # Distinct numeric channels have different dynamics. A single global
            # winner lets an energy transient dictate every motor-related rate.
            # Reconsider after each observed transition. Waiting for a periodic
            # boundary can keep an obsolete method for 31 more actions after a
            # change. A relative margin prevents switching on nearly tied losses.
            for i in range(CHANNELS):
                best = min(range(3), key=lambda m: self.channel_losses[m][i])
                current = self.channel_methods[i]
                if self.channel_losses[best][i] < .95 * self.channel_losses[current][i]:
                    self.channel_methods[i] = best
            self.selected = min(range(3), key=lambda i: self.losses[i])
        return observed_error, old != self.channel_methods

    def state(self):
        return dict(models=[m.state() for m in self.models], losses=self.losses[:],
                    selected=self.selected, adaptive=self.adaptive, samples=self.samples,
                    channel_losses=deepcopy(self.channel_losses), channel_methods=self.channel_methods[:],
                    last_trace=deepcopy(self.last_trace), error=self.error)

    @classmethod
    def restore(cls, state):
        obj = cls(state["adaptive"])
        obj.models = [ActionModel.restore(m) for m in state["models"]]
        obj.losses = state["losses"][:]
        obj.selected, obj.samples = state["selected"], state["samples"]
        obj.channel_losses = deepcopy(state["channel_losses"])
        obj.channel_methods = state["channel_methods"][:]
        obj.last_trace, obj.error = deepcopy(state["last_trace"]), state["error"]
        return obj


class MainController:
    def __init__(self, seed):
        self.model = ActionModel()
        self.rng = DeterministicRng(seed ^ 0xAB31)
        self.previous = None
        self.action = [0.0] * MOTORS
        self.error = 0.0
        self.weights = [.45, .35, .20]  # novelty, predicted change, effort
        self.recall_id = None

    def observe(self, sensors):
        sensors = vector(sensors, CHANNELS)
        if self.previous is not None:
            self.error = self.model.learn(self.previous, self.action, sensors)
            # Bounded priorities: prediction instability shifts effort/novelty balance.
            novelty = clip(.45 - self.error * 2, .2, .5)
            self.weights = [novelty, .35, .65 - novelty]

    def recall(self, sensors, memories):
        """Bounded attention over protected numeric experience, never observer labels."""
        self.recall_id = None
        if not memories:
            return None
        indices = {len(memories) - 1}
        for _ in range(min(16, len(memories))):
            indices.add(min(len(memories) - 1, int(self.rng.uniform(0, len(memories)))))
        index = min(sorted(indices), key=lambda i: sum(
            (a - b) ** 2 for a, b in zip(sensors, memories[i]["representation"]["sensors"])))
        memory = memories[index]
        self.recall_id = memory["memory_id"]
        return memory["representation"]["action"][:]

    def propose(self, sensors, neural_motors, recalled_action=None):
        candidates = [list(neural_motors), [self.rng.uniform(-.7, .7) for _ in range(MOTORS)]]
        if recalled_action is not None:
            candidates.append(list(vector(recalled_action, MOTORS)))
        best = None
        for action in candidates:
            prediction = self.model.predict(sensors, action)
            change = sum(abs(a - b) for a, b in zip(sensors, prediction)) / CHANNELS
            effort = sum(v * v for v in action) / MOTORS
            score = self.weights[1] * change - self.weights[2] * effort
            score += self.weights[0] * self.rng.uniform(0, .12)
            if best is None or score > best[0]:
                best = (score, action)
        return best[1]

    def record_action(self, sensors, action):
        self.previous = list(sensors)
        self.action = list(action)

    def state(self):
        return dict(model=self.model.state(), rng=self.rng.state, previous=self.previous,
                    action=self.action, error=self.error, weights=self.weights, recall_id=self.recall_id)

    @classmethod
    def restore(cls, state):
        obj = cls(1)
        obj.model = ActionModel.restore(state["model"])
        obj.rng.state = state["rng"]
        for name in ("previous", "action", "error", "weights", "recall_id"):
            setattr(obj, name, deepcopy(state[name]))
        return obj


class InternalResearcher:
    """Only signals and prior selected actions; candidate model is fallible."""
    def __init__(self, seed):
        self.model = AdaptiveModel()
        self.rng = DeterministicRng(seed ^ 0xCA51)
        self.previous = None
        self.action = [0.0] * MOTORS
        self.hypothesis = None
        self.next_id = 1
        self.error = 0.0
        self.exploration = .35

    def observe(self, sensors, tick):
        events = []
        if self.previous is not None:
            self.error, changed = self.model.learn(self.previous, self.action, sensors)
            if changed:
                events.append(dict(kind="method_changed", source="researcher",
                                   method="learning_rate_selection", selected=self.model.selected,
                                   channel_methods=self.model.channel_methods[:],
                                   prequential_losses=self.model.losses[:]))
            self.exploration = clip(.2 + math.sqrt(self.error), .2, .6)
        if self.hypothesis is None and tick % 100 == 0 and self.model.samples >= 64:
            action = [self.rng.uniform(-self.exploration, self.exploration) for _ in range(MOTORS)]
            delta = self.rng.uniform(-.04, .04)
            # Approximation: gain changes act like scaled actions. No simulator oracle.
            prediction = self.model.predict(sensors, [clip(v * (1 + delta), -1, 1) for v in action])
            self.hypothesis = dict(id=self.next_id, phase="model_trial", proposed_tick=tick,
                                   parameter="strength", delta=delta, action=action,
                                   prediction=prediction, uncertainty=self.error,
                                   age=0, elapsed=0, mismatch=0.0)
            self.next_id += 1
            events.extend([dict(kind="hypothesis_proposed", source="researcher",
                                hypothesis=deepcopy(self.hypothesis)),
                           dict(kind="model_trial_completed", source="researcher",
                                hypothesis_id=self.hypothesis["id"], prediction=prediction,
                                uncertainty=self.error, domain="internal_model")])
        return events

    def propose(self):
        return (self.hypothesis["action"][:] if self.hypothesis else
                [self.rng.uniform(-self.exploration, self.exploration) for _ in range(MOTORS)])

    def record_action(self, sensors, action):
        self.previous = list(sensors)
        self.action = list(action)

    def state(self):
        return dict(model=self.model.state(), rng=self.rng.state, previous=self.previous,
                    action=self.action, hypothesis=deepcopy(self.hypothesis), next_id=self.next_id,
                    error=self.error, exploration=self.exploration)

    @classmethod
    def restore(cls, state):
        obj = cls(1)
        obj.model = AdaptiveModel.restore(state["model"])
        obj.rng.state = state["rng"]
        for name in ("previous", "action", "hypothesis", "next_id", "error", "exploration"):
            setattr(obj, name, deepcopy(state[name]))
        return obj


class ActionArbiter:
    def __init__(self):
        self.sequence = 0
        self.last = None

    def select(self, main, research, recovering):
        main, research = vector(main, MOTORS), vector(research, MOTORS)
        action = [0.0] * MOTORS if recovering else [clip(.75 * a + .25 * b, -.8, .8)
                                                     for a, b in zip(main, research)]
        self.sequence += 1
        self.last = dict(kind="action_selected", source="arbiter", decision=self.sequence,
                         main=list(main), research=list(research), action=action,
                         recovery_veto=recovering)
        return action, deepcopy(self.last)
