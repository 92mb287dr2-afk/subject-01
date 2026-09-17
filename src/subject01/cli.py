from __future__ import annotations

import argparse
import json
import shlex

from .core import SimulationConfig
from .runtime import SimulationRuntime


HELP = """commands:
  spawn X Y [KIND]       add an object at the next tick boundary
  impulse ID IX IY       apply an impulse
  remove ID              remove an object
  gravity VALUE          change vertical gravity
  status                 print current state
  save                   save a snapshot now
  help                   show this help
  quit                   save and stop
"""


def _dispatch(runtime: SimulationRuntime, line: str) -> bool:
    parts = shlex.split(line)
    if not parts:
        return True
    command, *args = parts
    if command == "spawn":
        x, y = float(args[0]), float(args[1])
        kind = args[2] if len(args) > 2 else "unknown"
        print(runtime.submit("spawn_object", {"x": x, "y": y, "kind": kind}))
    elif command == "impulse":
        print(
            runtime.submit(
                "impulse",
                {"object_id": args[0], "ix": float(args[1]), "iy": float(args[2])},
            )
        )
    elif command == "remove":
        print(runtime.submit("remove_object", {"object_id": args[0]}))
    elif command == "gravity":
        print(runtime.submit("set_gravity", {"gravity_y": float(args[0])}))
    elif command == "status":
        print(json.dumps(runtime.status(), indent=2, sort_keys=True))
    elif command == "save":
        runtime.save_snapshot()
        print("snapshot saved")
    elif command == "help":
        print(HELP)
    elif command in {"quit", "exit"}:
        return False
    else:
        print("unknown command; type help")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Subject-01 observer console")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--dt", type=float, default=0.05)
    args = parser.parse_args()
    runtime = SimulationRuntime.open(
        args.data_dir, SimulationConfig(seed=args.seed, dt=args.dt)
    )
    runtime.start()
    print("Subject-01 world online. Subject status: PRE-BIRTH.")
    print(HELP)
    try:
        keep_running = True
        while keep_running:
            try:
                keep_running = _dispatch(runtime, input("observer> "))
            except (ValueError, IndexError, KeyError) as exc:
                print(f"invalid command: {exc}")
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        runtime.stop()
        print("world stopped; snapshot saved")


if __name__ == "__main__":
    main()
