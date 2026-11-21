"""Last-resort camera path: a laptop's webcam feeds the Pi over the network.

Use this only if no camera works on the Pi itself. The face model, the
state machine, the sensor and the alerts still run on QNX; only the lens is
borrowed. Tell the judges that this is what is happening.

On the Pi (needs only Python, no extra packages):

    python3 qnx/frame_relay.py receive --out /tmp/frame.jpg
    python3 -m readtheroom --frame-file /tmp/frame.jpg

On the laptop (needs opencv-python):

    python qnx/frame_relay.py send --url http://PI_ADDRESS:8090 --camera 0

The receiver has been tested on a laptop (posting JPEGs to it and reading them
back with FileFrameSource). It has not been run on QNX. Video crosses the
network unencrypted, so use it on a network you control.
"""
from __future__ import annotations

import argparse
import http.client
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_FRAME = 4 * 1024 * 1024


def is_jpeg(data: bytes) -> bool:
    return len(data) > 4 and data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"


def write_atomic(path: str, data: bytes) -> None:
    """The reader must never see half a file: write beside it, then rename."""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def receive(args) -> None:
    state = {"count": 0, "since": time.monotonic()}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keeps one connection open for all frames

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if self.path != "/frame" or not 0 < length <= MAX_FRAME:
                self.close_connection = True
                self.send_error(400)
                return
            data = self.rfile.read(length)
            ok = is_jpeg(data)
            if ok:
                write_atomic(args.out, data)
                state["count"] += 1
                elapsed = time.monotonic() - state["since"]
                if elapsed >= 5:
                    print(f"{state['count'] / elapsed:.1f} frames per second", flush=True)
                    state["count"], state["since"] = 0, time.monotonic()
            self.send_response(204 if ok else 400)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Waiting for frames on port {args.port}; writing {args.out}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def send(args) -> None:
    import cv2

    target = urlsplit(args.url)
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open camera {args.camera}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    period = 1.0 / args.rate
    connection = None
    print(f"Sending camera {args.camera} to {args.url} at up to {args.rate:g} frames "
          "per second. Ctrl+C to stop.")
    try:
        while True:
            started = time.monotonic()
            ok, frame = cap.read()
            if ok:
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, args.quality])
            if ok:
                try:
                    if connection is None:
                        connection = http.client.HTTPConnection(
                            target.hostname, target.port or 80, timeout=3)
                    connection.request("POST", "/frame", body=jpeg.tobytes(),
                                       headers={"Content-Type": "image/jpeg"})
                    connection.getresponse().read()
                except (OSError, http.client.HTTPException) as exc:
                    print(f"Could not reach the Pi: {exc}")
                    connection = None
                    time.sleep(1.0)
            time.sleep(max(0.0, period - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass


def main() -> None:
    p = argparse.ArgumentParser(prog="frame_relay")
    sub = p.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("receive", help="run on the Pi")
    r.add_argument("--out", default="/tmp/frame.jpg")
    r.add_argument("--host", default="0.0.0.0")
    r.add_argument("--port", type=int, default=8090)
    s = sub.add_parser("send", help="run on the laptop with the camera")
    s.add_argument("--url", required=True, help="http://PI_ADDRESS:8090")
    s.add_argument("--camera", type=int, default=0)
    s.add_argument("--width", type=int, default=1280)
    s.add_argument("--height", type=int, default=720)
    s.add_argument("--rate", type=float, default=15.0)
    s.add_argument("--quality", type=int, default=80)
    args = p.parse_args()
    receive(args) if args.mode == "receive" else send(args)


if __name__ == "__main__":
    main()
