"""Reconstruct logged action-model matrices and verify their actual computations."""
from .organism import clip


class ModelAudit:
    def __init__(self):
        self.models = {name: dict(weights=[[0.0] * 21 for _ in range(16)], version=0, last=None)
                       for name in ("controller", "researcher-0", "researcher-1", "researcher-2")}
        self.inferences = 0

    def accept(self, event):
        if event["kind"] == "observer_override_confirmed" and event["operation"] == "edit_model_weights":
            self.models[event["target"]]["weights"] = [row[:] for row in event["replacement"]]
            self.models[event["target"]]["last"] = None
            return
        if event["kind"] not in ("model_inference", "model_learned"):
            return
        model = self.models[event["source"]]
        features, weights = event["features"], model["weights"]
        if event["kind"] == "model_inference":
            if event["weights_version"] != model["version"]:
                raise ValueError("Inference matrix version does not match journal history")
            prediction = [clip(event["sensors"][i] + sum(w * v for w, v in zip(row, features)))
                          for i, row in enumerate(weights)]
            if prediction != event["result"]:
                raise ValueError("Recorded model inference does not match its inputs and weights")
            model["last"] = event
            self.inferences += 1
        else:
            prior = model["last"]
            if not prior or prior["features"] != features or event["weights_version"] != model["version"] + 1:
                raise ValueError("Learning update has no matching prediction")
            norm = sum(v * v for v in features)
            for i, row in enumerate(weights):
                delta = event["rate"] * (event["following"][i] - prior["result"][i]) / norm
                for j in range(21):
                    row[j] = clip(row[j] + delta * features[j], -2, 2)
            model["version"] += 1

    def check_store(self, store):
        after = 0
        while batches := store.journal(after, 500):
            for batch in batches:
                for event in batch["events"]:
                    self.accept(event)
            after = batches[-1]["seq"]
        return self
