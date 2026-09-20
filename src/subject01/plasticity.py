"""Bounded structural proposals using only the network's sensory experience."""
from copy import deepcopy

from .organism import PredictiveNetwork, clip


class TrialNetwork(PredictiveNetwork):
    def __init__(self, seed):
        super().__init__(seed)
        self.examples = []
        self.proposal = None
        self.next_proposal = 1

    def step(self, sensors, tick, allow_growth=False):
        if self.previous_hidden is not None:
            self.examples = (self.examples + [dict(hidden=self.previous_hidden[:],
                prediction=self.previous_prediction[:], observed=list(sensors))])[-24:]
        motors, events = super().step(sensors, tick, allow_growth=False)
        if self.proposal is None and tick % 80 == 0 and len(self.examples) >= 16:
            existing = {edge["id"] for edge in self.edges}
            best = None
            for h in range(12):
                for p in range(8):
                    if f"h{h}:p{p}" in existing:
                        continue
                    # Fit on the older half; evaluate on the newer half. A bounded
                    # prediction readout is the internal model, not the true world.
                    training, validation = self.examples[:12], self.examples[12:]
                    numerator = sum(e["hidden"][h] * (e["observed"][p] - e["prediction"][p]) for e in training)
                    denominator = sum(e["hidden"][h] ** 2 for e in training) + 1e-8
                    weight = clip(numerator / denominator, -.15, .15)
                    baseline = sum((e["observed"][p] - e["prediction"][p]) ** 2 for e in validation)
                    changed = sum((e["observed"][p] - clip(e["prediction"][p] + weight * e["hidden"][h])) ** 2
                                  for e in validation)
                    score = (baseline - changed) / len(validation) - abs(weight) * .00001
                    if best is None or score > best[0]:
                        best = (score, h, p, weight)
            if best and best[0] > 1e-8:
                score, h, p, weight = best
                self.proposal = dict(id=self.next_proposal, phase="model_trial", proposed_tick=tick,
                    source=f"h{h}", target=f"p{p}", weight=weight, score=score,
                    baseline_error=self.error, age=0, elapsed=0)
                self.next_proposal += 1
                events.append(dict(kind="network_model_trial", proposal=deepcopy(self.proposal), domain="internal_model"))
        return motors, events

    def state(self):
        state = super().state()
        state["plasticity"] = deepcopy(dict(examples=self.examples, proposal=self.proposal,
                                           next_proposal=self.next_proposal))
        return state

    @classmethod
    def restore(cls, state):
        obj = super().restore(state)
        p = state["plasticity"]
        obj.examples = deepcopy(p["examples"])
        obj.proposal = deepcopy(p["proposal"])
        obj.next_proposal = p["next_proposal"]
        return obj
