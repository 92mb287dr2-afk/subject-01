"""Local-only browser observer. Run: python -m subject01.observer."""
from __future__ import annotations

import argparse
from collections import deque
from copy import deepcopy
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import uuid
from urllib.parse import parse_qs, urlparse
import webbrowser

from .core import SimulationConfig
from .organism import ObserverCore
from .runtime import EventStore, SimulationRuntime


class ObserverRuntime(SimulationRuntime):
    def __init__(self, core, store):
        super().__init__(core, store)
        self.frames = deque(maxlen=240)
        self.session_id = str(uuid.uuid4())
        self.neural_path = store.data_dir / "neural-events.jsonl"

    @classmethod
    def open(cls, data_dir, config=None):
        store = EventStore(Path(data_dir))
        state = store.load_snapshot()
        core = ObserverCore.from_state(state) if state else ObserverCore(config)
        if core.config.width < 4 or core.config.height < 4:
            raise ValueError("observer world must be at least 4 x 4")
        store.save_manifest_once(core.birth_manifest())
        runtime = cls(core, store)
        if state is None:
            for x, y in ((0.2, 0.3), (0.7, 0.55), (0.6, 0.2), (0.35, 0.7)):
                runtime.submit("spawn_object", dict(
                    x=x * core.config.width, y=y * core.config.height, kind="stone"))
            runtime.save_snapshot()
        return runtime

    def _frame(self):
        core = self.core
        return dict(tick=core.tick_index, time=core.simulation_time,
                    width=core.config.width, height=core.config.height,
                    body=deepcopy(core.body), brain=core.brain.state(),
                    objects=[asdict(core.objects[key]) for key in sorted(core.objects)],
                    events=deepcopy(core.last_neural_events))

    def _after_step(self, applied):
        frame = self._frame()
        self.frames.append(frame)
        # Batch writes per tick, not one filesystem sync per neural transmission.
        with self.neural_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(session=self.session_id, tick=frame["tick"],
                                         events=frame["events"]), allow_nan=False) + "\n")

    def telemetry(self, after=-1):
        with self._lock:
            ring = list(self.frames)
            if after < 0:
                chosen = ring[-1:]
            else:
                chosen = [frame for frame in ring if frame["tick"] > after][:40]
            current = chosen[-1] if chosen else (ring[-1] if ring else self._frame())
            return deepcopy(dict(
                session=self.session_id, running=self._running.is_set(),
                error=self.fatal_error, dt=self.core.config.dt,
                snapshot=current,
                # Keep all per-tick signal and weight events since the cursor.
                batches=[dict(tick=f["tick"], events=f["events"]) for f in chosen],
                cursor=current["tick"],
                gap=bool(after >= 0 and ring and after < ring[0]["tick"] - 1),
            ))


class ObserverServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, runtime, port=8765):
        self.runtime = runtime
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), ObserverHandler)


class ObserverHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, data, content_type="application/json; charset=utf-8"):
        body = json.dumps(data, allow_nan=False).encode() if isinstance(data, dict) else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _valid_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self._valid_host():
            self._send(403, {"error": "local host required"})
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/frames":
                after = int(parse_qs(parsed.query).get("after", ["-1"])[0])
                self._send(200, self.server.runtime.telemetry(after))
            elif parsed.path == "/api/session":
                self._send(200, {"token": self.server.token})
            elif parsed.path == "/api/state":
                self._send(200, self.server.runtime.status())
            elif parsed.path == "/api/journal" and hasattr(self.server.runtime.store, "journal"):
                after = int(parse_qs(parsed.query).get("after", ["0"])[0])
                with self.server.runtime._lock:
                    batches = self.server.runtime.store.journal(after)
                self._send(200, dict(batches=batches, cursor=batches[-1]["seq"] if batches else after))
            elif parsed.path == "/api/neural-log":
                self._download_log()
            elif parsed.path in ("/", "/app.js", "/style.css"):
                name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[parsed.path]
                mime = {"index.html": "text/html", "app.js": "text/javascript",
                        "style.css": "text/css"}[name]
                self._send(200, files("subject01").joinpath("web", name).read_bytes(),
                           mime + "; charset=utf-8")
            else:
                self._send(404, {"error": "not found"})
        except (ValueError, KeyError) as exc:
            self._send(400, {"error": str(exc)})

    def _download_log(self):
        runtime = self.server.runtime
        if hasattr(runtime.store, "journal"):
            with runtime._lock:
                last = runtime.store.db.execute("SELECT max(seq) FROM journal").fetchone()[0] or 0
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Disposition", 'attachment; filename="committed-events.jsonl"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            after = 0
            while after < last:
                with runtime._lock:
                    batches = runtime.store.journal(after)
                if not batches:
                    break
                for batch in batches:
                    if batch["seq"] > last:
                        break
                    self.wfile.write(json.dumps(batch, allow_nan=False).encode() + b"\n")
                    after = batch["seq"]
            self.close_connection = True
            return
        path = self.server.runtime.neural_path
        if not path.exists():
            self._send(200, b"", "application/x-ndjson")
            return
        # Read a fixed-size prefix: the world keeps appending independently.
        # Stop at a complete line so the exported JSONL remains parseable.
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 131072))
            tail = handle.read()
            last_newline = tail.rfind(b"\n")
            size = size - len(tail) + last_newline + 1 if last_newline >= 0 else 0
            handle.seek(0)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Disposition", 'attachment; filename="neural-events.jsonl"')
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            remaining = size
            while remaining:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_POST(self):
        if not self._valid_host():
            self._send(403, {"error": "local host required"})
            return
        port = self.server.server_port
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            self._send(403, {"error": "origin rejected"})
            return
        if not secrets.compare_digest(self.headers.get("X-Observer-Token", ""), self.server.token):
            self._send(403, {"error": "observer token required"})
            return
        if not self.server.runtime._running.is_set():
            self._send(503, {"error": self.server.runtime.fatal_error or "world not running"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 32768 if self.path == "/api/override/preview" else 4096
            if length < 1 or length > limit:
                raise ValueError(f"body must be 1..{limit} bytes")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("JSON object required")
            if self.path == "/api/command":
                if hasattr(self.server.runtime, "metadata"):
                    result = self.server.runtime.submit(data["kind"], data["payload"], data.get("request_id"))
                else:
                    result = self.server.runtime.submit(data["kind"], data["payload"])
                self._send(202, result)
            elif self.path == "/api/override/preview" and hasattr(self.server.runtime, "preview_override"):
                self._send(200, self.server.runtime.preview_override(data["operation"], data["target"], data.get("replacement")))
            elif self.path == "/api/override/confirm" and hasattr(self.server.runtime, "confirm_override"):
                self._send(200, self.server.runtime.confirm_override(data["token"], data["confirmation"]))
            elif self.path == "/api/save":
                self.server.runtime.save_snapshot()
                self._send(200, {"saved": True})
            elif self.path in ("/api/pause", "/api/resume") and hasattr(self.server.runtime, "set_paused"):
                self._send(200, self.server.runtime.set_paused(self.path == "/api/pause"))
            else:
                self._send(404, {"error": "not found"})
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            self._send(400, {"error": str(exc)})
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            self._send(503, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="Subject-01 live observer")
    parser.add_argument("--data-dir")
    parser.add_argument("--candidate", action="store_true", help="Run the durable pre-birth candidate")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.candidate:
        from .candidate import CandidateRuntime
        runtime = CandidateRuntime.open(args.data_dir or "./data-candidate", SimulationConfig())
    else:
        runtime = ObserverRuntime.open(args.data_dir or "./data-observer", SimulationConfig())
    try:
        server = ObserverServer(runtime, args.port)
        runtime.start()
    except BaseException:
        runtime.stop()
        raise
    url = f"http://127.0.0.1:{server.server_port}"
    mode = "Durable candidate" if args.candidate else "Diagnostic model"
    print(f"Observer: {url}\n{mode}, PRE-BIRTH. Ctrl+C saves and stops.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.stop()


if __name__ == "__main__":
    main()
