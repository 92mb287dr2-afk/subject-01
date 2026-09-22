"""Run the preregistered numeric learning protocol; emit JSON, never tune thresholds."""
import json
from pathlib import Path

from subject01.core import DeterministicRng, SimulationConfig
from subject01.development import ActionModel, AdaptiveModel
from subject01.life import LifeCore
from subject01.organism import clip
from subject01.continuity import code_hash, digest


def mse(predicted, observed):
    return sum((a - b) ** 2 for a, b in zip(predicted, observed)) / len(predicted)


def evaluate():
    protocol = json.loads(Path(__file__).with_name("protocol.json").read_text())
    rows = []
    for seed in protocol["seeds"]:
        rng = DeterministicRng(seed)
        core = LifeCore(SimulationConfig(seed=seed))
        trained, frozen = ActionModel(), ActionModel()
        train = protocol["embodied_train"]
        learned_error = baseline_error = 0.0
        for i in range(train + protocol["embodied_holdout"]):
            before = core.sensations()
            action = [rng.uniform(-.7, .7) for _ in range(4)]
            prediction = trained.predict(before, action)
            baseline = frozen.predict(before, action)
            core._move(action)
            core._resources(action)
            after = core.sensations()
            if i < train:
                trained.learn(before, action, after)
            else:
                learned_error += mse(prediction, after)
                baseline_error += mse(baseline, after)
        adaptive, fixed = AdaptiveModel(), AdaptiveModel(adaptive=False)
        adaptive_error = fixed_error = 0.0
        selections = []
        train = protocol["method_train"]
        for i in range(train + protocol["method_holdout"]):
            sensors = [rng.uniform(.2, .8) for _ in range(16)]
            action = [rng.uniform(-.8, .8) for _ in range(4)]
            gain = (.15 if (i // 80) % 2 == 0 else -.15)
            following = [clip(sensors[j] + gain * action[j % 4] + rng.uniform(-.001, .001)) for j in range(16)]
            a, b = adaptive.predict(sensors, action), fixed.predict(sensors, action)
            if i >= train:
                adaptive_error += mse(a, following)
                fixed_error += mse(b, following)
            adaptive.learn(sensors, action, following)
            fixed.learn(sensors, action, following)
            if not selections or selections[-1] != adaptive.selected:
                selections.append(adaptive.selected)
        rows.append(dict(seed=seed, embodied_error_ratio=learned_error / baseline_error,
                         adaptive_error_ratio=adaptive_error / fixed_error,
                         selected_methods=selections))
    embodied = sum(r["embodied_error_ratio"] for r in rows) / len(rows)
    methods = sum(r["adaptive_error_ratio"] for r in rows) / len(rows)
    return dict(code_hash=code_hash(), protocol_hash=digest(protocol), protocol=protocol, results=rows, embodied_mean_ratio=embodied,
                adaptive_mean_ratio=methods,
                passed=embodied <= protocol["embodied_max_mean_error_ratio_to_frozen"]
                and methods <= protocol["method_max_mean_error_ratio_to_fixed"])


if __name__ == "__main__":
    result = evaluate()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
