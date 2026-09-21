"""Single-writer, atomic continuity. No pickle, wall time, or silent recovery reset."""
from __future__ import annotations

from contextlib import contextmanager
from collections import OrderedDict
from collections.abc import Sequence
from copy import deepcopy
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import sqlite3
import uuid
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


class DiskMemories(Sequence):
    """Numeric records loaded on demand; uncommitted appends belong to one step."""
    def __init__(self, store, count, hasher, last_id=0):
        self.store = store
        self.count = count
        self.hasher = hasher.copy()
        self.last_id = last_id
        self.pending = []
        self.edits = {}
        self.deleted = {}

    def __len__(self):
        return self.count - len(self.deleted) + len(self.pending)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self))) ]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        retained = self.count - len(self.deleted)
        if index >= retained:
            return deepcopy(self.pending[index - retained])
        for position in sorted(self.deleted.values()):
            if position <= index:
                index += 1
        record = self.store.memory_at(index)
        return deepcopy(self.edits.get(record["memory_id"], record))

    def __deepcopy__(self, memo):
        return list(self)

    def fork(self):
        other = DiskMemories(self.store, self.count, self.hasher, self.last_id)
        other.pending = deepcopy(self.pending)
        other.edits, other.deleted = deepcopy(self.edits), self.deleted.copy()
        return other

    def change(self, memory_id, replacement=None):
        row = self.store.db.execute("SELECT position FROM memory_positions WHERE memory_id=?", (memory_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown memory")
        if replacement is None:
            self.deleted[memory_id] = row[0]
        else:
            self.edits[memory_id] = deepcopy(replacement)

    def append(self, record):
        if record["memory_id"] <= self.last_id:
            raise ValueError("Protected memory IDs must increase")
        if len(self):
            self.hasher.update(b",")
        self.hasher.update(encode([record["memory_id"], record["integrity_hash"]]))
        self.pending.append(deepcopy(record))
        self.last_id = record["memory_id"]

    def manifest(self):
        if self.edits or self.deleted:
            h = hashlib.sha256(b"[")
            for i, record in enumerate(self):
                if i:
                    h.update(b",")
                h.update(encode([record["memory_id"], record["integrity_hash"]]))
            h.update(b"]")
            return dict(count=len(self), root=h.hexdigest())
        h = self.hasher.copy()
        h.update(b"]")
        return dict(count=len(self), root=h.hexdigest())


class ContinuityStore:
    FORMAT = 2
    ARCHIVE_BATCHES = 32
    HOT_BATCHES = 64

    def __init__(self, directory):
        self.data_dir = Path(directory).resolve()
        self.lock = DirectoryLock(self.data_dir)
        self.db = None
        self.memory_cache = OrderedDict()
        self.memory_view = None
        self.commit_uncertain = False
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
            self.db.execute("PRAGMA temp_store=FILE")
            self.db.execute("PRAGMA cache_size=-4096")
            self.db.execute("CREATE TEMP TABLE memory_positions(position INTEGER PRIMARY KEY, memory_id INTEGER UNIQUE)")
            self.db.execute("PRAGMA temp.cache_size=-2048")
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
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='archive_files'").fetchone():
                for filename, required in self.db.execute("SELECT path,max(offset+size) FROM archive_files GROUP BY path"):
                    if Path(filename).stat().st_size < required:
                        raise ValueError("Truncated journal archive pack; state untouched")
            self.load(lazy=True)
        except BaseException:
            self.close()
            raise

    @contextmanager
    def transaction(self):
        self.commit_uncertain = False
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.fault_hook("after_begin")
            yield
            self.fault_hook("before_commit")
            self.db.execute("COMMIT")
            self.commit_uncertain = True
        except BaseException:
            self.commit_uncertain = not self.db.in_transaction
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        self.fault_hook("after_commit")

    def load(self, lazy=False):
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
            self._reindex_memories()
            if self.memory_view.manifest() != state["life"].pop("memory_manifest"):
                raise ValueError("Protected memory / checkpoint boundary mismatch")
            state["life"]["memories"] = self.memory_view.fork() if lazy else list(self.memory_view)
        return state

    @staticmethod
    def _decode_memory(row):
        memory_id, payload, checksum = row
        raw = zlib.decompress(payload)
        if hashlib.sha256(raw).hexdigest() != checksum:
            raise ValueError("Protected memory checksum mismatch")
        record = json.loads(raw)
        if record["memory_id"] != memory_id:
            raise ValueError("Protected memory identity mismatch")
        if record["integrity_hash"] != digest({k: v for k, v in record.items() if k != "integrity_hash"}):
            raise ValueError("Protected memory integrity failure")
        return record

    def _reindex_memories(self):
        self.db.execute("DELETE FROM memory_positions")
        self.memory_cache.clear()
        h, count, last_id = hashlib.sha256(b"["), 0, 0
        for row in self.db.execute("SELECT memory_id,payload,hash FROM memories ORDER BY memory_id"):
            record = self._decode_memory(row)
            if count:
                h.update(b",")
            h.update(encode([row[0], record["integrity_hash"]]))
            self.db.execute("INSERT INTO memory_positions VALUES(?,?)", (count, row[0]))
            count, last_id = count + 1, row[0]
        self.memory_view = DiskMemories(self, count, h, last_id)

    def memory_at(self, position):
        if position in self.memory_cache:
            self.memory_cache.move_to_end(position)
            return deepcopy(self.memory_cache[position])
        row = self.db.execute("SELECT m.memory_id,m.payload,m.hash FROM memory_positions p "
                              "JOIN memories m ON m.memory_id=p.memory_id WHERE p.position=?", (position,)).fetchone()
        if row is None:
            raise ValueError("Protected memory position missing")
        record = self._decode_memory(row)
        self.memory_cache[position] = record
        if len(self.memory_cache) > 128:
            self.memory_cache.popitem(last=False)
        return deepcopy(record)

    @contextmanager
    def memory_export(self):
        """A read-only SQLite snapshot; no full archive or writer lock in RAM."""
        reader = sqlite3.connect((self.data_dir / "continuity.sqlite3").as_uri() + "?mode=ro", uri=True)
        try:
            reader.execute("PRAGMA cache_size=-2048")
            reader.execute("BEGIN")
            row = reader.execute("SELECT payload,hash FROM checkpoint WHERE id=1").fetchone()
            raw = zlib.decompress(row[0])
            if hashlib.sha256(raw).hexdigest() != row[1]:
                raise ValueError("Checkpoint checksum mismatch")
            state = json.loads(raw)
            if self.format != 2:
                raise ValueError("Paged memory export requires storage format 2")
            manifest = dict(kind="memory_export", tick=state["tick_index"],
                            continuity=state["continuity"], memory_manifest=state["life"]["memory_manifest"])
            records = (self._decode_memory(r) for r in reader.execute(
                "SELECT memory_id,payload,hash FROM memories ORDER BY memory_id"))
            yield manifest, records
        finally:
            reader.close()

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
        lazy = isinstance(records, DiskMemories)
        compact["life"]["memory_manifest"] = records.manifest() if lazy else self._memory_manifest(records)
        changed = []
        for record in (records.pending + list(records.edits.values())) if lazy else records:
            raw = encode(record)
            prior = self.db.execute("SELECT hash FROM memories WHERE memory_id=?", (record["memory_id"],)).fetchone() if self.format == 2 else None
            if prior is None or prior[0] != hashlib.sha256(raw).hexdigest():
                if record["integrity_hash"] != digest(
                        {k: v for k, v in record.items() if k != "integrity_hash"}):
                    raise ValueError("Protected memory integrity failure")
                changed.append((record["memory_id"], zlib.compress(raw),
                                hashlib.sha256(raw).hexdigest(), deepcopy(record)))
        if lazy or self.format == 1:
            removed = set(records.deleted) if lazy else set()
        else:
            ids = {r["memory_id"] for r in records}
            removed = {row[0] for row in self.db.execute("SELECT memory_id FROM memories") if row[0] not in ids}
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
            if self.format == 2 and sequence % self.ARCHIVE_BATCHES == 0:
                self._archive_journal()
            self.db.execute("INSERT OR REPLACE INTO checkpoint VALUES(1,?,?,?)",
                            (state["tick_index"], zlib.compress(raw_state),
                             hashlib.sha256(raw_state).hexdigest()))
            self.fault_hook("after_checkpoint")
        if self.format == 2:
            records = state["life"]["memories"]
            if isinstance(records, DiskMemories) and not records.edits and not records.deleted:
                for i, record in enumerate(records.pending, records.count):
                    self.db.execute("INSERT INTO memory_positions VALUES(?,?)", (i, record["memory_id"]))
                self.memory_view = DiskMemories(self, len(records), records.hasher, records.last_id)
            else:
                self._reindex_memories()
        return state

    def journal(self, after=0, limit=100):
        return [json.loads(raw) for _, raw, _, _ in
                islice(self._journal_rows(after), min(max(limit, 1), 500))]

    def offload_journal(self, destination):
        """Offline pack export: fsync file before atomically replacing DB payloads.

        Caller owns the directory's writer lock. A crash before the SQL commit
        leaves an unreferenced pack, never a dangling committed reference.
        """
        if self.format != 2:
            raise ValueError("Journal offload requires storage format 2")
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        pack = destination / ("subject01-" + uuid.uuid4().hex + ".pack")
        # The bounded temporary SQL index avoids accumulating pack offsets in RAM.
        self.db.execute("CREATE TEMP TABLE IF NOT EXISTS pack_offsets(first_seq INTEGER PRIMARY KEY, offset INTEGER, size INTEGER)")
        self.db.execute("DELETE FROM pack_offsets")
        count = 0
        with pack.open("xb") as handle:
            for first, payload, checksum in self.db.execute(
                    "SELECT first_seq,payload,hash FROM journal_archives WHERE length(payload)>0 ORDER BY first_seq"):
                if hashlib.sha256(zlib.decompress(payload)).hexdigest() != checksum:
                    raise ValueError("Journal archive checksum mismatch")
                self.db.execute("INSERT INTO pack_offsets VALUES(?,?,?)", (first, handle.tell(), len(payload)))
                handle.write(payload)
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            fd = os.open(destination, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.fault_hook("after_pack_sync")
        if not count:
            pack.unlink()
            return dict(blocks=0, bytes=0, pack=None)
        with pack.open("rb") as handle:
            for offset, size, checksum in self.db.execute(
                    "SELECT p.offset,p.size,a.hash FROM pack_offsets p JOIN journal_archives a USING(first_seq)"):
                handle.seek(offset)
                if hashlib.sha256(zlib.decompress(handle.read(size))).hexdigest() != checksum:
                    raise ValueError("Archive pack read-back verification failed; original retained")
        with self.transaction():
            self.db.execute("CREATE TABLE IF NOT EXISTS archive_files(first_seq INTEGER PRIMARY KEY, path TEXT NOT NULL, offset INTEGER NOT NULL, size INTEGER NOT NULL)")
            self.db.execute("INSERT INTO archive_files SELECT first_seq,?,offset,size FROM pack_offsets", (str(pack),))
            self.fault_hook("after_pack_references")
            self.db.execute("UPDATE journal_archives SET payload=x'' WHERE first_seq IN (SELECT first_seq FROM pack_offsets)")
            self.fault_hook("after_pack_release")
        # Verify the committed indirection before reclaiming freed SQLite pages.
        self.verify_journal()
        self.db.execute("VACUUM")
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return dict(blocks=count, bytes=pack.stat().st_size, pack=str(pack))

    def _archive_payload(self, first, payload):
        if payload:
            return payload
        row = self.db.execute("SELECT path,offset,size FROM archive_files WHERE first_seq=?", (first,)).fetchone()
        if row is None:
            raise ValueError("Missing journal archive reference")
        with Path(row[0]).open("rb") as handle:
            handle.seek(row[1])
            payload = handle.read(row[2])
        if len(payload) != row[2]:
            raise ValueError("Truncated journal archive pack")
        return payload

    def _archive_journal(self):
        """Lossless cross-batch compression, in the same transaction as the tick.

        Keep the checkpoint boundary hot and limit work in a world transaction.
        SQLite can reuse the deleted pages; no external rename or unsafe unlink.
        """
        if self.db.execute("SELECT count(*) FROM journal").fetchone()[0] < self.HOT_BATCHES + self.ARCHIVE_BATCHES:
            return
        rows = self.db.execute("SELECT seq,payload,hash,previous_hash FROM journal "
                               "ORDER BY seq LIMIT ?", (self.ARCHIVE_BATCHES,)).fetchall()
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
            # Older schema-2 writers used 256-batch blocks. This lower bound
            # includes a containing old/new block while seeking by the primary
            # key, instead of scanning the entire archive for each live cursor.
            for first, last, payload, checksum in self.db.execute(
                    "SELECT first_seq,last_seq,payload,hash FROM journal_archives "
                    "WHERE first_seq>=? AND last_seq>? ORDER BY first_seq",
                    (max(1, after - 255), after)):
                raw = zlib.decompress(self._archive_payload(first, payload))
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--migrate-memory-layout", metavar="DATA_DIRECTORY")
    group.add_argument("--offload-journal", metavar="DATA_DIRECTORY")
    parser.add_argument("--destination", help="Permanent directory on the archive drive")
    args = parser.parse_args()
    if args.offload_journal and not args.destination:
        parser.error("--offload-journal requires --destination")
    store = ContinuityStore(args.migrate_memory_layout or args.offload_journal)
    try:
        if args.offload_journal:
            print(json.dumps(store.offload_journal(args.destination), indent=2))
        else:
            store.migrate_memory_layout()
            print("Memory layout: 2. Identity, code hash, laws and journal unchanged.")
    finally:
        store.close()
