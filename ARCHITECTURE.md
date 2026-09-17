# Architecture v0.1

```text
Observer CLI
    |
    | isolated intervention queue
    v
SimulationRuntime ---- EventStore
    |                    |-- events.jsonl
    |                    |-- latest.snapshot.json
    |                    +-- birth-manifest.json
    v
SimulationCore
    |-- deterministic clock
    |-- deterministic PRNG
    |-- world state
    +-- tick-boundary command application
```

## Independent responsibilities

- **SimulationCore** is a pure deterministic state machine. It cannot read wall-clock time, files or observer metadata.
- **SimulationRuntime** owns the background thread and maps wall time to fixed simulation ticks.
- **EventStore** owns append-only event logging and atomic snapshots.
- **Observer CLI** submits interventions and reads copies of state; blocking user input never pauses the world.

## Information firewall

The future brain may receive only declared sensor channels and emit declared motor channels. It must not access object IDs, debug labels, intervention records, source files, logs, wall-clock metadata or privileged world state.

## Tick contract

1. Drain commands in ascending event-ID order.
2. Apply every command at the current tick boundary.
3. Integrate physics using a fixed `dt`.
4. Increment the simulation tick.
5. Persist scheduled snapshots outside the pure core.

## Birth gate

Subject-01 remains PRE-BIRTH until world, body, nervous interface, controller and instrumentation tests pass. v0.1 deliberately has empty sensor and motor channel lists.
