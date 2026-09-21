"""Measure bounded Python allocations and indexed recall on a large memory archive."""
import gc
import json
import tempfile
import time
import tracemalloc

from subject01.candidate import CandidateRuntime
from subject01.continuity import ContinuityStore, code_hash, digest


def measure(count):
    with tempfile.TemporaryDirectory() as folder:
        runtime = CandidateRuntime.open(folder)
        runtime.advance()
        state = runtime.status()
        runtime.stop()
        template = state["life"]["memories"][0]
        records = []
        for i in range(count):
            record = {**template, "memory_id": i + 1}
            record["integrity_hash"] = digest({k: v for k, v in record.items() if k != "integrity_hash"})
            records.append(record)
        state["life"]["memories"] = records
        state["life"]["next_memory_id"] = count + 1
        store = ContinuityStore(folder)
        store.commit(state, [{"kind": "storage_fixture"}])
        store.close()
        del state, records, template, runtime, store
        gc.collect()
        tracemalloc.start()
        start = time.perf_counter()
        runtime = CandidateRuntime.open(folder)
        restore_seconds = time.perf_counter() - start
        restore_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        timings = []
        try:
            for _ in range(100):
                start = time.perf_counter()
                runtime.advance()
                timings.append(time.perf_counter() - start)
            return dict(records=count, restore_seconds=restore_seconds,
                        restore_python_peak_bytes=restore_peak,
                        mean_step_ms=sum(timings) / len(timings) * 1000,
                        cached_records=len(runtime.store.memory_cache),
                        passed=restore_peak < 4 * 1024 * 1024 and len(runtime.store.memory_cache) <= 128)
        finally:
            runtime.stop()


if __name__ == "__main__":
    report = dict(code_hash=code_hash(), scope="Python allocations during restore; native SQLite caches are separately limited; fixture creation excluded",
                  results=[measure(count) for count in (26000, 104000)])
    report["passed"] = all(row["passed"] for row in report["results"])
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
