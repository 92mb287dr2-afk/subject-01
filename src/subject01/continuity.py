"""Single-writer, atomic continuity. No pickle, wall time, or silent recovery reset."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import sqlite3
import zlib


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def code_hash():
    root = Path(__file__).parent
    entries = [(p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
               for p in sorted(root.rglob("*"))
               if p.suffix in {".py", ".html", ".js", ".css"}]
    return digest(entries)


class DirectoryLock:
    def __init__(self, directory):
        self.path = Path(directory).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        self.handle = (self.path / "writer.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0, 2)
                if self.handle.tell() == 0:
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("This data directory already has a running writer") from exc

    def close(self):
        if self.handle.closed:
            return
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


class ContinuityStore:
    FORMAT = 2

    def __init__(self, directory):
        self.data_dir = Path(directory).resolve()
        self.lock = DirectoryLock(self.data_dir)
        self.db = None
        self._memories = {}
        self.fault_hook = lambda phase: None  # tests inject real process termination here
        try:
            if any((self.data_dir / name).exists() for name in
                   ("latest.snapshot.json", "birth-manifest.json")):
                raise ValueError("Legacy directory: use a separate candidate data directory")
            path = self.data_dir / "continuity.sqlite3"
            existing = path.exists()
            self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False,
                                      timeout=0.5)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            if existing:
                if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Continuity integrity check failed; no reset performed")
                if self.db.execute("PRAGMA user_version").fetchone()[0] not in (1, self.FORMAT):
                    raise ValueError("Incompatible continuity schema; no reset performed")
            else:
                self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE checkpoint(id INTEGER PRIMARY KEY CHECK(id=1),
                    tick INTEGER NOT NULL, payload BLOB NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE commands(request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                    receipt TEXT NOT NULL);
                CREATE TABLE journal(seq INTEGER PRIMARY KEY, tick INTEGER NOT NULL,
                    payload BLOB NOT NULL, hash TEXT NOT NULL, previous_hash TEXT NOT NULL);
                CREATE TABLE memories(memory_id INTEGER PRIMARY KEY, payload BLOB NOT NULL,
                    hash TEXT NOT NULL);
                CREATE TABLE journal_archives(first_seq INTEGER PRIMARY KEY, last_seq INTEGER NOT NULL,
                    payload BLOB NOT NULL, hash TEXT NOT NULL);
                PRAGMA user_version=2;
                COMMIT;
                """)
            self.format = self.db.execute("PRAGMA user_version").fetchone()[0]
            self.load()
        except BaseException:
            self.close()
            raise

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.fault_hook("after_begin")
            yield
            self.fault_hook("before_commit")
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        self.fault_hook("after_commit")

    def load(self):
        row = self.db.execute("SELECT payload,hash FROM checkpoint WHERE id=1").fetchone()
        if row is None:
            # An existing, empty schema can occur before the initial transaction.
            if self.db.execute("SELECT count(*) FROM journal").fetchone()[0]:
                raise ValueError("Missing checkpoint; refusing to create a replacement")
            return None
        raw = zlib.decompress(row[0])
        if hashlib.sha256(raw).hexdigest() != row[1]:
            raise ValueError("Checkpoint checksum mismatch")
        state = json.loads(raw)
        last = self.db.execute("SELECT hash FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        if last is None or state["continuity"]["journal_hash"] != last[0]:
            raise ValueError("Checkpoint / journal boundary mismatch")
        if self.format == 2:
            records = []
            for memory_id, payload, checksum in self.db.execute(
                    "SELECT memory_id,payload,hash FROM memories ORDER BY memory_id"):
                raw_memory = zlib.decompress(payload)
                if hashlib.sha256(raw_memory).hexdigest() != checksum:
                    raise ValueError("Protected memory checksum mismatch")
                record = json.loads(raw_memory)
                if record["memory_id"] != memory_id:
                    raise ValueError("Protected memory identity mismatch")
                if record["integrity_hash"] != digest(
                        {k: v for k, v in record.items() if k != "integrity_hash"}):
                    raise ValueError("Protected memory integrity failure")
                records.append(record)
            if self._memory_manifest(records) != state["life"].pop("memory_manifest"):
                raise ValueError("Protected memory / checkpoint boundary mismatch")
            state["life"]["memories"] = records
            self._memories = {r["memory_id"]: deepcopy(r) for r in records}
        return state

    @staticmethod
    def _memory_manifest(records):
        ids = [r["memory_id"] for r in records]
        if ids != sorted(set(ids)):
            raise ValueError("Protected memory IDs must be unique and increasing")
        return dict(count=len(records), root=digest(
            [(r["memory_id"], r["integrity_hash"]) for r in records]))

    def _prepare_memories(self, state):
        """Detached compact checkpoint and changed rows; never mutate caller records."""
        records = state["life"]["memories"]
        compact = {**state, "life": {**state["life"]}}
        compact["life"].pop("memories")
        compact["life"]["memory_manifest"] = self._memory_manifest(records)
        changed = []
        for record in records:
            if self._memories.get(record["memory_id"]) != record:
                if record["integrity_hash"] != digest(
                        {k: v for k, v in record.items() if k != "integrity_hash"}):
                    raise ValueError("Protected memory integrity failure")
                raw = encode(record)
                changed.append((record["memory_id"], zlib.compress(raw),
                                hashlib.sha256(raw).hexdigest(), deepcopy(record)))
        removed = self._memories.keys() - {r["memory_id"] for r in records}
        return compact, changed, removed

    def _write_memories(self, changed, removed):
        self.db.executemany("DELETE FROM memories WHERE memory_id=?", [(i,) for i in removed])
        self.db.executemany("INSERT OR REPLACE INTO memories VALUES(?,?,?)",
                            [r[:3] for r in changed])
        self.fault_hook("after_memories")

    def migrate_memory_layout(self):
        """Explicit, atomic storage-only migration; identity/code/laws remain pinned."""
        if self.format == 2:
            return
        state = self.load()
        if state is None:
            raise ValueError("Cannot migrate an uninitialized store")
        compact, changed, removed = self._prepare_memories(state)
        raw = encode(compact)
        with self.transaction():
            self.db.execute("CREATE TABLE memories(memory_id INTEGER PRIMARY KEY, "
                            "payload BLOB NOT NULL, hash TEXT NOT NULL)")
            self.db.execute("CREATE TABLE journal_archives(first_seq INTEGER PRIMARY KEY, "
                            "last_seq INTEGER NOT NULL, payload BLOB NOT NULL, hash TEXT NOT NULL)")
            self._write_memories(changed, removed)
            self.db.execute("UPDATE checkpoint SET payload=?,hash=? WHERE id=1",
                            (zlib.compress(raw), hashlib.sha256(raw).hexdigest()))
            self.fault_hook("after_checkpoint")
            self.db.execute("PRAGMA user_version=2")
        self.format = 2
        self.load()

    def receipt(self, request_id, request):
        row = self.db.execute("SELECT request_hash,receipt FROM commands WHERE request_id=?",
                              (request_id,)).fetchone()
        if row is None:
            return None
        if row[0] != digest(request):
            raise ValueError("Request ID was already used for a different operation")
        return json.loads(row[1])

    def commit(self, state, events, command=None):
        """Checkpoint, all tick events, and optional command receipt share one commit."""
        prior = self.db.execute("SELECT seq,hash FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        sequence, previous = (prior[0] + 1, prior[1]) if prior else (1, "0" * 64)
        batch = dict(seq=sequence, tick=state["tick_index"],
                     origin_id=state["continuity"]["origin_id"], events=events)
        raw_events = encode(batch)
        chain = hashlib.sha256(previous.encode() + raw_events).hexdigest()
        state["continuity"]["journal_hash"] = chain
        changed, removed = [], set()
        compact = state
        if self.format == 2:
            compact, changed, removed = self._prepare_memories(state)
        raw_state = encode(compact)
        if len(raw_state) > 8 * 1024 * 1024:
            raise OSError("Checkpoint budget reached (8 MiB); technical pause, no memories deleted")
        with self.transaction():
            if self.format == 2:
                self._write_memories(changed, removed)
            if command:
                request_id, request, receipt = command
                self.db.execute("INSERT INTO commands VALUES(?,?,?)",
                                (request_id, digest(request), encode(receipt).decode()))
            self.db.execute("INSERT INTO journal VALUES(?,?,?,?,?)",
                            (sequence, state["tick_index"], zlib.compress(raw_events), chain, previous))
            self.fault_hook("after_events")
            if self.format == 2 and sequence % 256 == 0:
                self._archive_journal()
            self.db.execute("INSERT OR REPLACE INTO checkpoint VALUES(1,?,?,?)",
                            (state["tick_index"], zlib.compress(raw_state),
                             hashlib.sha256(raw_state).hexdigest()))
            self.fault_hook("after_checkpoint")
        for memory_id in removed:
            self._memories.pop(memory_id)
        for memory_id, _, _, record in changed:
            self._memories[memory_id] = record
        return state

    def journal(self, after=0, limit=100):
        return [json.loads(raw) for _, raw, _, _ in
                islice(self._journal_rows(after), min(max(limit, 1), 500))]

    def _archive_journal(self):
        """Lossless cross-batch compression, in the same transaction as the tick.

        Always leave at least 256 hot batches, including the checkpoint boundary.
        SQLite can reuse the deleted pages; no external rename or unsafe unlink.
        """
        if self.db.execute("SELECT count(*) FROM journal").fetchone()[0] < 512:
            return
        rows = self.db.execute("SELECT seq,payload,hash,previous_hash FROM journal "
                               "ORDER BY seq LIMIT 256").fetchall()
        packed = [[seq, json.loads(zlib.decompress(payload)), checksum, parent]
                  for seq, payload, checksum, parent in rows]
        raw = encode(packed)
        self.db.execute("INSERT INTO journal_archives VALUES(?,?,?,?)",
                        (rows[0][0], rows[-1][0], zlib.compress(raw), hashlib.sha256(raw).hexdigest()))
        self.fault_hook("after_archive_insert")
        self.db.execute("DELETE FROM journal WHERE seq<=?", (rows[-1][0],))
        self.fault_hook("after_archive_delete")

    def _journal_rows(self, after=0):
        if self.format == 2:
            for first, last, payload, checksum in self.db.execute(
                    "SELECT first_seq,last_seq,payload,hash FROM journal_archives "
                    "WHERE last_seq>? ORDER BY first_seq", (after,)):
                raw = zlib.decompress(payload)
                if hashlib.sha256(raw).hexdigest() != checksum:
                    raise ValueError("Journal archive checksum mismatch")
                rows = json.loads(raw)
                if [r[0] for r in rows] != list(range(first, last + 1)):
                    raise ValueError("Journal archive sequence mismatch")
                for seq, batch, chain, parent in rows:
                    if seq != batch["seq"]:
                        raise ValueError("Journal archive batch identity mismatch")
                    if seq > after:
                        yield seq, encode(batch), chain, parent
        for seq, payload, checksum, parent in self.db.execute(
                "SELECT seq,payload,hash,previous_hash FROM journal WHERE seq>? ORDER BY seq", (after,)):
            yield seq, zlib.decompress(payload), checksum, parent

    def verify_journal(self):
        previous = "0" * 64
        sequence = 0
        for seq, raw, checksum, parent in self._journal_rows():
            expected = hashlib.sha256(previous.encode() + raw).hexdigest()
            if seq != sequence + 1 or parent != previous or expected != checksum:
                raise ValueError("Journal integrity check failed")
            sequence = seq
            previous = checksum
        return previous

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        self.lock.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Explicit continuity storage maintenance")
    parser.add_argument("--migrate-memory-layout", required=True, metavar="DATA_DIRECTORY")
    args = parser.parse_args()
    store = ContinuityStore(args.migrate_memory_layout)
    try:
        store.migrate_memory_layout()
        print("Memory layout: 2. Identity, code hash, laws and journal unchanged.")
    finally:
        store.close()
