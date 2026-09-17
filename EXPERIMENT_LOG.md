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
