"""Autonomous whole-core transfer experiment; independent of UI and wall time."""
import json
import argparse
from pathlib import Path

from subject01.core import SimulationConfig
from subject01.life import LifeCore
from subject01.continuity import code_hash, digest


def run(seed, task, adaptive, protocol):
    core = LifeCore(SimulationConfig(seed=seed))
    core.researcher.model.adaptive = adaptive
    for _ in range(protocol["training_ticks"]):
        core.step()
    if task == "severe_damage_recovery":
        core.submit("damage_body", {"amount": 1})
    elif task == "unseen_motor_permutation":
        original = core._move
        order = protocol["motor_permutation"]
        # Test-only unfamiliar actuator mapping. Neither learner receives order.
        core._move = lambda action: original([action[i] for i in order])
    error = 0.0
    methods = set()
    for _ in range(protocol["transfer_ticks"]):
        core.step()
        error += core.researcher.error
        methods.add(core.researcher.model.selected)
    return dict(error=error / protocol["transfer_ticks"], methods=sorted(methods),
                memories=len(core.memories), final_mode=core.body["mode"])


def evaluate(protocol_path=None):
    protocol = json.loads((Path(protocol_path) if protocol_path else Path(__file__).with_name("organism_protocol.json")).read_text())
    rows = []
    for seed in protocol["seeds"]:
        for task in protocol["new_tasks"]:
            adaptive, fixed = (run(seed, task, enabled, protocol) for enabled in (True, False))
            rows.append(dict(seed=seed, task=task, adaptive=adaptive, fixed=fixed,
                             ratio=adaptive["error"] / fixed["error"]))
    ratio = sum(row["ratio"] for row in rows) / len(rows)
    return dict(code_hash=code_hash(), protocol_hash=digest(protocol), protocol=protocol, results=rows, mean_ratio=ratio,
                passed=ratio <= protocol["max_mean_adaptive_to_fixed_error_ratio"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol")
    args = parser.parse_args()
    result = evaluate(args.protocol)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
