"""Explicit first birth, never conversion of a diagnostic history."""
import argparse
import json
import os
from pathlib import Path
import uuid

from .continuity import ContinuityStore, DirectoryLock, code_hash, digest, encode
from .life import LifeCore, LAWS
from .candidate import CandidateRuntime


def authority_directory():
    return Path.home() / ".subject01"


def release_manifest():
    path = Path(__file__).with_name("release.json")
    if not path.exists():
        raise ValueError("Release validation report not installed; birth is closed")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not report.get("ready") or report.get("code_hash") != code_hash() or report.get("laws_hash") != digest(LAWS):
        raise ValueError("Release report does not approve this exact code and laws; birth is closed")
    if set(report.get("gates", {})) != {f"C{i:02}" for i in range(1, 22)} or not all(v is True for v in report["gates"].values()):
        raise ValueError("Incomplete acceptance report; birth is closed")
    return report


def read_identity():
    path = authority_directory() / "identity.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def upgrade_candidate(data_dir, prior_hash, confirmation):
    """Owner-requested pre-birth code update, never an official-life upgrade."""
    if confirmation != "ОБНОВИТЬ КАНДИДАТ":
        raise ValueError("Exact candidate update confirmation required")
    store = ContinuityStore(data_dir)
    try:
        state = store.load(lazy=True)
        if not state or state["continuity"]["status"] != "PRE-BIRTH / CANDIDATE":
            raise ValueError("Only pre-birth candidate code may be updated")
        if state["continuity"]["code_hash"] != prior_hash:
            raise ValueError("Saved code changed since the update preview")
        core = LifeCore.from_state(state, lazy_memory=store.format == 2)
        if not core.kernel["intact"]:
            raise ValueError("Destroyed kernel cannot be replaced by an update")
        state["continuity"]["code_hash"] = code_hash()
        store.commit(state, [dict(kind="candidate_code_updated", source="owner", before=prior_hash, after=code_hash())])
    finally:
        store.close()


def _write_identity(record):
    folder = authority_directory()
    temporary = folder / "identity.pending.json"
    with temporary.open("wb") as handle:
        handle.write(encode(record))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, folder / "identity.json")
    if os.name != "nt":
        descriptor = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def prepare(data_dir):
    report = release_manifest()
    lock = DirectoryLock(authority_directory())
    try:
        existing = read_identity()
        if existing:
            if existing["status"] == "pending" and existing["data_dir"] == str(Path(data_dir).resolve()):
                return existing
            raise ValueError("An identity is already reserved or born; another birth is forbidden")
        target = Path(data_dir).resolve()
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError("Birth requires a new empty directory, not diagnostic data")
        plan = dict(status="pending", origin_id=str(uuid.uuid4()), data_dir=str(target),
                    code_hash=code_hash(), laws_hash=digest(LAWS), report_hash=digest(report))
        plan["confirmation"] = "РОДИТЬ SUBJECT-01 " + plan["origin_id"][:8]
        _write_identity(plan)
        return plan
    finally:
        lock.close()


def confirm(confirmation, fault_hook=lambda phase: None):
    report = release_manifest()
    lock = DirectoryLock(authority_directory())
    try:
        plan = read_identity()
        if not plan or confirmation != plan["confirmation"]:
            raise ValueError("Exact birth confirmation required")
        if plan["code_hash"] != code_hash() or plan["report_hash"] != digest(report):
            raise ValueError("Birth plan changed; no birth performed")
        if plan["status"] == "born":
            return plan  # Idempotent acknowledgement, never reset a lost/destroyed body.
        fault_hook("after_reservation")
        store = ContinuityStore(plan["data_dir"])
        try:
            state = store.load(lazy=True)
            if state is None:
                core = LifeCore()
                for x, y in ((.2, .3), (.7, .55), (.6, .2), (.35, .7)):
                    core.submit("spawn_object", dict(x=x * core.config.width, y=y * core.config.height, kind="stone"))
                state = core.state()
                state["continuity"] = dict(schema_version=1, origin_id=plan["origin_id"],
                    subject_id="subject-01", status="LIVING", code_hash=plan["code_hash"],
                    laws_hash=plan["laws_hash"], journal_hash="0" * 64, birth_report=report)
                store.fault_hook = fault_hook
                store.commit(state, [dict(kind="subject_born", source="owner", report_hash=plan["report_hash"])])
            elif state["continuity"]["origin_id"] != plan["origin_id"] or state["continuity"]["status"] != "LIVING":
                raise ValueError("Target contains another history; conversion forbidden")
        finally:
            store.close()
        fault_hook("after_birth_commit")
        plan["status"] = "born"
        _write_identity(plan)
        return plan
    finally:
        lock.close()


class SubjectRuntime(CandidateRuntime):
    STATUS = "LIVING"
    ALLOW_CREATE = False

    @classmethod
    def open(cls, data_dir=None, config=None):
        identity = read_identity()
        if not identity or identity["status"] != "born":
            raise ValueError("First birth has not been confirmed")
        target = Path(data_dir or identity["data_dir"]).resolve()
        if str(target) != identity["data_dir"]:
            raise ValueError("Only the registered official directory may run")
        runtime = super().open(target, config)
        if runtime.metadata["origin_id"] != identity["origin_id"]:
            runtime.stop()
            raise ValueError("Official identity mismatch")
        return runtime


def main():
    parser = argparse.ArgumentParser(description="One explicit Subject-01 birth")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", metavar="NEW_DATA_DIRECTORY")
    group.add_argument("--confirm", metavar="EXACT_PHRASE")
    args = parser.parse_args()
    result = prepare(args.prepare) if args.prepare else confirm(args.confirm)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
