# Observer v0.2 — implementation record

Status: PRE-BIRTH / DIAGNOSTIC. The official birth gate remains closed.

Architecture: ObserverCore extends the original pure core; PredictiveNetwork receives
only eight floats and emits four motor floats plus privileged observer telemetry.
ObserverRuntime owns simulation timing, snapshots and the complete neural JSONL journal.
A loopback-only HTTP server serves packaged static assets and read-only snapshots.
Browser layout, graph dragging, zoom and filtering never enter the sensor stream.

Plasticity: online next-sensor delta prediction. Only hidden→prediction edges adapt.
One missing edge with maximum absolute local error gradient may grow every 40 steps.
Each computed nonzero edge transmission, nonzero weight update and creation is emitted
with a monotonic sequence number and simulation tick. Initial edges have created_tick=0.

Persistence: body, network nodes/edges, previous hidden state/prediction, error,
RNG state and event sequence are included in snapshots. Neural history is not copied
into snapshots; it is append-only JSONL. Hard-crash replay and journal compaction
remain future work. Separate data directory prevents converting the original birth
protocol into a diagnostic organism by accident.

Rendering: top-down world, humanoid glyph (not articulated joints), canvas graph;
brightness = activation magnitude, thickness = weight magnitude, color = channel group.
Particles represent computed transmissions; recent creations highlighted gold.
Multiple tick batches arrive per poll and animate together; this is not a faithful
millisecond conduction delay. UI lists last 200 events per type; download provides all
recorded events. Reduced-motion preference disables moving particles only.

Security: loopback binding, Host validation, same-origin POST validation, random
per-process token, bounded request body, no arbitrary paths, strict CSP, no CDN.

Verification: deterministic continuation, learning, signal equality, snapshot copying,
input rejection, persisted sequence continuity, independent clock, HTTP protections;
Chromium desktop/mobile integration, node selection, live editing, save, reconnect,
responsive overflow and browser-error capture in CI.
