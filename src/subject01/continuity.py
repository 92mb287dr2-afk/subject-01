"""Single-writer, atomic continuity. No pickle, wall time, or silent recovery reset."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
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
    FORMAT = 1

    def __init__(self, directory):
        self.data_dir = Path(directory).resolve()
        self.lock = DirectoryLock(self.data_dir)
        self.db = None
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
                if self.db.execute("PRAGMA user_version").fetchone()[0] != self.FORMAT:
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
                PRAGMA user_version=1;
                COMMIT;
                """)
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
        return state

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
        raw_state = encode(state)
        with self.transaction():
            if command:
                request_id, request, receipt = command
                self.db.execute("INSERT INTO commands VALUES(?,?,?)",
                                (request_id, digest(request), encode(receipt).decode()))
            self.db.execute("INSERT INTO journal VALUES(?,?,?,?,?)",
                            (sequence, state["tick_index"], zlib.compress(raw_events), chain, previous))
            self.fault_hook("after_events")
            self.db.execute("INSERT OR REPLACE INTO checkpoint VALUES(1,?,?,?)",
                            (state["tick_index"], zlib.compress(raw_state),
                             hashlib.sha256(raw_state).hexdigest()))
            self.fault_hook("after_checkpoint")
        return state

    def journal(self, after=0, limit=100):
        rows = self.db.execute("SELECT payload FROM journal WHERE seq>? ORDER BY seq LIMIT ?",
                               (after, min(max(limit, 1), 500))).fetchall()
        return [json.loads(zlib.decompress(row[0])) for row in rows]

    def verify_journal(self):
        previous = "0" * 64
        for payload, checksum, parent in self.db.execute(
                "SELECT payload,hash,previous_hash FROM journal ORDER BY seq"):
            expected = hashlib.sha256(previous.encode() + zlib.decompress(payload)).hexdigest()
            if parent != previous or expected != checksum:
                raise ValueError("Journal integrity check failed")
            previous = checksum
        return previous

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        self.lock.close()
