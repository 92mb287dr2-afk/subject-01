# Subject-01 Experiment Log

## Day 0 — Foundation implemented

Status: **PRE-BIRTH**

Implemented:

- deterministic fixed-timestep world core;
- serializable deterministic PRNG;
- ordered hot intervention queue;
- runtime object insertion, impulse, removal and gravity changes;
- autonomous background world loop independent of observer input;
- append-only JSONL intervention log;
- atomic snapshots and recovery;
- immutable-once-created birth manifest;
- baseline tests for determinism, tick boundaries and snapshot restoration.

Deliberately absent:

- organism body;
- nervous interface;
- brain/controller;
- semantic sensor labels;
- language;
- scalar survival reward;
- unrestricted self-modifying source code.

Next gate:

1. Run the automated foundation tests.
2. Add checksums/event replay validation.
3. Add a minimal body with internal energy and proprioception.
4. Define the sensor/motor firewall.
5. Only then prepare the first controlled birth candidate.

Primary research question remains:

Can a predictive plastic controller, given only embodied sensorimotor experience, develop internal representations that distinguish controllable body dynamics from externally caused dynamics?


## 2026-09-18 — Accepted design contract (documentation only)

Status: **PRE-BIRTH / DIAGNOSTIC**.

The Day 0 entry above is historical. The repository now also contains the browser
observer and diagnostic 32-node predictive model documented in OBSERVER_DESIGN.md.
It does not implement the complete organism discussed with the owner.

Recorded the owner's choices in [docs/SUBJECT_CONTRACT.md](docs/SUBJECT_CONTRACT.md):
one history, an uninformed internal researcher, limited body adaptation, learned
internal trials, proposals through shared action selection, adaptable learning
methods, shared experience but separate models, protected consolidated memory,
revisable beliefs, time paused while powered off, and a surviving recovery kernel.
Observer access remains full, with separate explicit confirmation to override
protected memory/kernel; no initial observer knowledge is supplied to the organism.

Added [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) with proposed verification gates and
the first work package: durable continuity before researcher development.
Technical implementation proposals are distinguished from accepted user choices.
No organism was born; no runtime, brain, saved state or memory was changed.
