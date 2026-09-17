"""Local-only browser observer. Run: python -m subject01.observer."""
from __future__ import annotations

import argparse
from collections import deque
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import secrets
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
                    objects=core.state()["objects"], events=deepcopy(core.last_neural_events))

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
            if length < 1 or length > 4096:
                raise ValueError("body must be 1..4096 bytes")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("JSON object required")
            if self.path == "/api/command":
                result = self.server.runtime.submit(data["kind"], data["payload"])
                self._send(202, result)
            elif self.path == "/api/save":
                self.server.runtime.save_snapshot()
                self._send(200, {"saved": True})
            else:
                self._send(404, {"error": "not found"})
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            self._send(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="Subject-01 live observer")
    parser.add_argument("--data-dir", default="./data-observer")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    runtime = ObserverRuntime.open(args.data_dir, SimulationConfig())
    server = ObserverServer(runtime, args.port)
    runtime.start()
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Observer: {url}\nDiagnostic model, PRE-BIRTH. Ctrl+C saves and stops.", flush=True)
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
