"""Bounded durability/performance soak. Uses only a temporary diagnostic directory."""
import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time
import sys
import zlib

from subject01.candidate import CandidateRuntime
from subject01.continuity import code_hash


def run(ticks):
    durations = []
    samples = []
    with tempfile.TemporaryDirectory(prefix="subject01-soak-") as folder:
        runtime = CandidateRuntime.open(folder)
        origin = runtime.metadata["origin_id"]
        try:
            for i in range(ticks):
                start = time.perf_counter()
                runtime.advance()
                durations.append(time.perf_counter() - start)
                if (i + 1) % 1000 == 0:
                    before = runtime.status()
                    runtime.stop()
                    runtime = CandidateRuntime.open(folder)
                    assert before == runtime.status()
                    assert runtime.metadata["origin_id"] == origin
                    samples.append(dict(tick=i + 1, memories=len(runtime.core.memories),
                                        mean_last_1000_ms=statistics.mean(durations[-1000:]) * 1000))
                    print(json.dumps(samples[-1]), file=sys.stderr, flush=True)
            runtime.store.verify_journal()
            totals, body = runtime.core.resource_totals, runtime.core.body
            for resource, initial in (("energy", 1), ("material", .5)):
                predicted = initial + totals[resource + "_in"] - totals[resource + "_out"] - totals[resource + "_spill"]
                assert abs(predicted - body[resource]) < 1e-8
            state_bytes = len(json.dumps(runtime.status()).encode())
            checkpoint_bytes = len(zlib.decompress(runtime.store.db.execute(
                "SELECT payload FROM checkpoint").fetchone()[0]))
            archive_count = runtime.store.db.execute("SELECT count(*) FROM journal_archives").fetchone()[0]
        finally:
            runtime.stop()
        disk_bytes = sum(p.stat().st_size for p in Path(folder).iterdir())
    return dict(code_hash=code_hash(), ticks=ticks, simulated_seconds=ticks * .05,
                mean_ms=statistics.mean(durations) * 1000,
                p95_ms=sorted(durations)[int(len(durations) * .95)] * 1000,
                max_ms=max(durations) * 1000, disk_bytes=disk_bytes,
                projected_gb_per_24h=disk_bytes / ticks * 20 * 86400 / 1e9,
                final_checkpoint_bytes=checkpoint_bytes, full_state_export_bytes=state_bytes,
                journal_archive_chunks=archive_count, samples=samples,
                continuity_and_balance_passed=True,
                real_time_budget_passed=statistics.mean(durations[-min(1000, ticks):]) < .05)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=10000)
    args = parser.parse_args()
    if args.ticks < 1:
        parser.error("ticks must be positive")
    result = run(args.ticks)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["real_time_budget_passed"] else 1)
