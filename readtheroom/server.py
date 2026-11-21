from __future__ import annotations

import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATIC = os.path.join(os.path.dirname(__file__), "static")
PAGES = {"/": "patient.html", "/nurse": "nurse.html", "/room": "room.html"}
MAX_BODY = 10_000
MAX_PHOTO = 4_000_000


class Hub:
    """Holds the latest state snapshot and wakes every connected screen."""

    def __init__(self):
        self._cond = threading.Condition()
        self._version = 0
        self._state: dict = {}
        self._clients = {"patient": 0, "nurse": 0}
        self._published_at: float | None = None

    def publish(self, state: dict) -> None:
        with self._cond:
            self._state = state
            self._version += 1
            self._published_at = time.monotonic()
            self._cond.notify_all()

    def wait(self, seen: int, timeout: float):
        with self._cond:
            if self._version == seen:
                self._cond.wait(timeout)
            return self._version, self._state

    def age(self) -> float | None:
        """Seconds since the main loop last published; None before the first."""
        with self._cond:
            if self._published_at is None:
                return None
            return time.monotonic() - self._published_at

    def join(self, role: str) -> None:
        with self._cond:
            self._clients[role] += 1

    def leave(self, role: str) -> None:
        with self._cond:
            self._clients[role] -= 1

    def count(self, role: str) -> int:
        with self._cond:
            return self._clients[role]


def make_handler(hub: Hub, app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the console for the main loop
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in PAGES:
                with open(os.path.join(STATIC, PAGES[url.path]), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif url.path == "/scene.jpg":
                jpeg = app.scene_photo()
                if jpeg is None:
                    self._json(404, {"error": "no scene photo yet"})
                else:
                    self._send(200, jpeg, "image/jpeg")
            elif url.path == "/preview.jpg":
                jpeg = app.preview_jpeg()
                if jpeg is None:
                    self._json(404, {"error": "preview is off"})
                else:
                    self._send(200, jpeg, "image/jpeg")
            elif url.path == "/health":
                age = hub.age()
                self._json(200, {"age_s": None if age is None else round(age, 2)})
            elif url.path == "/events":
                role = parse_qs(url.query).get("role", ["patient"])[0]
                self._events(role if role in ("patient", "nurse") else "patient")
            else:
                self._json(404, {"error": "not found"})

        def _events(self, role: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            hub.join(role)
            try:
                seen = -1
                while True:
                    version, state = hub.wait(seen, 1.0)
                    if version == seen:
                        # Main loop published nothing: keep the connection
                        # open but send no data, so screens can tell the
                        # device has stopped responding.
                        self.wfile.write(b": waiting\n\n")
                    else:
                        seen = version
                        self.wfile.write(b"data: " + json.dumps(state).encode() + b"\n\n")
                    self.wfile.flush()
            except OSError:
                pass  # screen closed or network dropped
            finally:
                hub.leave(role)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            path = urlparse(self.path).path
            limit = MAX_PHOTO if path == "/api/scene" else MAX_BODY
            if not 0 <= length <= limit:
                self.close_connection = True
                return self._json(400, {"error": "bad length"})
            raw = self.rfile.read(length)
            if path == "/api/scene":
                if not raw:
                    return self._json(400, {"error": "empty photo"})
                return self._json(200, {"ok": app.submit_scene(raw)})
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "bad json"})
            try:
                if path == "/api/preview":
                    return self._json(200, {"ok": app.set_preview(body.get("on") is True)})
                if path == "/api/scene/reset":
                    return self._json(200, {"ok": app.reset_scene()})
                if path == "/api/ack":
                    return self._json(200, {"ok": app.alerts.ack(int(body["id"]))})
                if path == "/api/calibrate":
                    return self._json(200, {"ok": app.request_calibration()})
                if path == "/api/debug/gaze" and app.simulate:
                    app.set_debug_gaze(body.get("label"))
                    return self._json(200, {"ok": True})
                if path == "/api/debug/temp" and app.simulate:
                    temp = body.get("temp_c")
                    temp = None if temp is None else float(temp)
                    if temp is not None and not math.isfinite(temp):
                        raise ValueError
                    app.set_debug_temp(temp)
                    return self._json(200, {"ok": True})
            except (KeyError, TypeError, ValueError, OverflowError):
                return self._json(400, {"error": "bad request"})
            self._json(404, {"error": "not found"})

    return Handler


def serve(hub: Hub, app, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(hub, app))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
