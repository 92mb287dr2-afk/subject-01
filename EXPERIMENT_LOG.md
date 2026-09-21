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

## 2026-09-19 — Durable development candidate

Implemented isolated PRE-BIRTH candidate storage, body trials, numeric controllers,
protected consolidation, recovery, and expanded observer UI. Formal birth remains closed.
Initial local validation: 35 tests pass; browser tests await CI. Preregistered limited
model experiments pass their mean-error thresholds, with per-seed results and scope
in [docs/RELEASE_STATUS.md](docs/RELEASE_STATUS.md). Structural network development,
complete observer edits, full-organism experiments, and long-duration budgets remain open.

## 2026-09-20 — Structural trials, recall, protected edits, transfer result

Implemented model-checked structural growth, gradual probation and resource-costed
forward repair. Added actual episodic recall, technical pause, exact-target protected
memory/model/recovery-policy edits, and exact action-model journal reconstruction.
Local engineering suite: 41 passed, 2 opt-in browser tests delegated to CI.

Whole-core transfer initially passed before recall (ratio 0.871711), but failed once
memory was actually used (1.030853). Per-channel method adaptation was then evaluated
on reserved new seeds and a different actuator permutation: ratio 0.979814 versus
the preregistered maximum 0.95. C18 remains failed; no threshold was relaxed.
CI now exposes this failure as a separate research gate. No formal birth occurred.

The 10,000-tick soak retained identity/state across ten restarts and preserved
resource balances, but the final 1,000 ticks averaged 63.16 ms against a 50 ms
budget. Removed redundant full-memory copies from normal step cloning and UI
frames, and duplicate checkpoint encoding. Added predecessor-immutability testing.
The local engineering suite now has 42 passing tests; a second soak verifies this optimization.

The second 10,000-tick soak passed the measured time budget: 27.77 ms mean,
35.23 ms p95, 33.45 ms over the final 1,000 ticks. All ten restarts and resource
balances passed. Persistent log growth is approximately 8.66 GB/day at 20 Hz.
CI also exposed an older observer race: live polling overwrote save confirmation
within 150 ms. User action notices now remain visible for four seconds; connection
and runtime errors still take priority. Added a browser assertion across multiple polls.

## 2026-09-21 — Protected memory storage and independently validated method selection

Schema 2 keeps protected memories in separate rows and pins their count/root in
the checkpoint. Only changed records are written. A 26,000-record fixture exceeds
the former 8 MiB full-state ceiling while the working checkpoint remains below
100 KB. Schema 1 remains readable/writable without silent migration; explicit
storage migration preserves identity, code/laws hashes and the journal. This is
not a code-compatibility migration and does not bypass the runner's code pin.

Journal chunks combine 256 batches using lossless cross-batch compression. Both
archive insertion and removal of hot rows share the world transaction. Process
termination tests cover both archive boundaries, migration boundaries, and new
memory insertion; pagination, complete chains and corruption refusal are tested.
Local suite: 48 passed, two opt-in browser tests remain for CI.

The previous 32-transition method-selection interval could retain an obsolete
method after a change. Selection now updates on each observed transition with a
5% relative loss margin against switching between nearly tied estimates. The
already-observed validation-2 set is development data: ratio 0.935440. Before
measuring again, local commit 36a6118 fixed validation-3 seeds 503/607/809 and the
new actuator permutation [1,3,0,2]. Its unchanged threshold is 0.95. All six results
are retained; mean ratio 0.868842 passes. One damage scenario still regresses;
most improvement comes from actuator permutation. No universal competence claim.

Raw initial and post-storage replay reports retain their actual source hashes.
CI now uses validation-3. Isolated probes still pass (body mean 0.793366, adaptive
method mean 0.677649). Formal birth remains closed: bounded RAM access to the
growing memory, archive budgets and final owner-reviewed readiness remain open.

The first schema-2 soak completed 10,000 ticks and ten restarts: mean 34.91 ms,
last 1,000 mean 33.57 ms, 36.57 MB on disk, but p95 58.12 ms and max 1,330.78 ms.
To limit work per transaction, archive blocks were reduced from 256 to 32 batches,
with 64 hot batches retained. Indexed cursor seeks still read old 256-batch blocks;
a compatibility test covers that boundary. The original soak report is retained.

The 32-batch replay completed 10,000 ticks, ten exact restart comparisons, full
journal verification and resource balances. Mean 31.62 ms, final 1,000 mean
32.47 ms, max 241.07 ms, p95 59.89 ms: the mean budget passes, strict per-tick
real time does not. There are 303 memories and 310 lossless archive blocks;
working checkpoint 127,389 bytes versus full export 295,800 bytes, disk 35.79 MB,
projection 6.18 GB/day at 20 Hz. Full report: endurance-storage-small-blocks.json.
Source f78747d passes all four GitHub CI jobs: 49 engineering tests on Linux and
Windows, real Chromium scenarios, and transfer ratio 0.868842. Final source hash
923cfe3c7aecee18a5ce768cf77f0d0a66c450e88f5c22bc83e35364a15c54cb
matches local soak and independently repeated CI transfer. No birth occurred.
