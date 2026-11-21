from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import traceback

from .alerts import AlertStore
from .calibration import Calibrator
from .config import NO, REST, YES, Config
from .features import Ema
from .prompts import load_zones
from .server import Hub, serve
from .session import Session
from .zones import ZoneClassifier


class App:
    def __init__(self, cfg: Config, zones: dict, data_dir: str, hub: Hub,
                 source=None, extractor=None, sensor=None, simulate: bool = False):
        self.cfg = cfg
        self.zones = zones
        self.hub = hub
        self.source = source
        self.extractor = extractor
        self.sensor = sensor
        self.simulate = simulate
        os.makedirs(data_dir, exist_ok=True)
        self._calibration_path = os.path.join(data_dir, "calibration.json")
        self.alerts = AlertStore(cfg.escalate_after_s, os.path.join(data_dir, "alerts.json"))
        self.session = Session(cfg, zones, self.alerts)
        self.classifier = ZoneClassifier()
        self.self_check: dict = {}
        targets = [(REST, "Look at the centre of the screen"),
                   (YES, "Look at YES on the left"),
                   (NO, "Look at NO on the right")]
        targets += [(z.id, f"Look at the {z.label}") for z in zones.values()]
        self._expected = {t[0] for t in targets}
        self.calibrator = Calibrator(targets, cfg.settle_s, cfg.record_s)
        self._want_calibration = False
        self._debug_gaze: str | None = REST
        self._temp: float | None = None
        self._humidity: float | None = None
        self._fps = 0.0
        self._ema = Ema(cfg.ema_alpha)
        self._last = (None, False)
        self._last_frame_at: float | None = None
        self._load_calibration()

    def _load_calibration(self) -> None:
        if self.simulate or not os.path.exists(self._calibration_path):
            return
        try:
            self.classifier.load(self._calibration_path)
        except Exception:  # any unreadable file just means "calibrate again"
            self.classifier.fitted = False
            return
        if self.classifier.labels != self._expected:  # zones file changed
            self.classifier.fitted = False

    # Called from web-server threads.
    def request_calibration(self) -> bool:
        if self.simulate:
            return False
        self._want_calibration = True
        return True

    def set_debug_gaze(self, label) -> None:
        self._debug_gaze = label if isinstance(label, str) else None

    def set_debug_temp(self, temp_c) -> None:
        self._temp = temp_c

    def _poll_sensor(self) -> None:
        while True:
            reading = self.sensor.read()
            self._temp, self._humidity = reading if reading else (None, None)
            time.sleep(2.0)

    def _perceive(self, now: float):
        """Returns (gaze label, face visible)."""
        if self.simulate:
            label = self._debug_gaze
            return (None, False) if label == "noface" else (label, True)
        frame = self.source.read()
        if frame is None:
            # No new frame yet: hold the last answer briefly, but never act on
            # a stale one. A stalled camera must read as "cannot see patient".
            stale = (self._last_frame_at is None
                     or now - self._last_frame_at > self.cfg.frame_stale_s)
            return (None, False) if stale else self._last
        self._last_frame_at = now
        face, feature = self.extractor(frame, int(now * 1000))
        feature = self._ema(feature)
        if self.calibrator.active:
            if self.calibrator.update(now, feature):
                self._finish_calibration(now)
            return None, face
        return self.classifier.predict(feature), face

    def _finish_calibration(self, now: float) -> None:
        self.session.reset(now)
        if self.calibrator.error:
            return  # any earlier calibration stays in use
        try:
            self.classifier.fit(self.calibrator.X, self.calibrator.y)
            self.self_check = self.classifier.self_check()
            self.classifier.save(self._calibration_path)
        except (ValueError, OSError) as exc:
            self.calibrator.error = f"Calibration failed: {exc}"

    def tick(self, now: float) -> None:
        started = time.monotonic()
        if self._want_calibration:
            self._want_calibration = False
            self.calibrator.start(now)
        label, face = self._perceive(now)
        self._last = (label, face)
        ready = self.simulate or self.classifier.fitted
        if self.calibrator.active or not ready:
            self.session.mark_face(now, face)
        else:
            self.session.update(now, label, face, self._temp)
        self.alerts.tick()
        lost = self.session.face_lost_s(now)
        self.hub.publish({
            "bed": self.cfg.bed,
            "time": time.time(),
            "zones": [{"id": z.id, "label": z.label} for z in self.zones.values()],
            "session": self.session.snapshot(now),
            "calibration": self.calibrator.snapshot(now),
            "calibrated": ready,
            "self_check": self.self_check,
            "alerts": self.alerts.list(),
            "status": {
                "face_ok": lost < self.cfg.face_ok_s,
                "face_lost": lost >= self.cfg.face_lost_notice_s,
                "temp_c": self._temp,
                "humidity": self._humidity,
                "tick_ms": round((time.monotonic() - started) * 1000, 1),
                "fps": round(self._fps, 1),
                "too_slow": 0 < self._fps < self.cfg.min_fps,
                "nurse_screens": self.hub.count("nurse"),
                "simulate": self.simulate,
            },
        })

    def run(self) -> None:
        if self.sensor is not None:
            threading.Thread(target=self._poll_sensor, daemon=True).start()
        period = 1.0 / self.cfg.loop_hz
        previous = None
        while True:
            now = time.monotonic()
            if previous is not None and now > previous:
                rate = 1.0 / (now - previous)
                self._fps = rate if self._fps == 0 else 0.9 * self._fps + 0.1 * rate
            previous = now
            try:
                self.tick(now)
            except Exception:  # one bad frame must not stop the device
                traceback.print_exc()
            time.sleep(max(0.0, period - (time.monotonic() - now)))


def _lan_address() -> str | None:
    """This machine's address on the local network (sends no packets)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            return probe.getsockname()[0]
    except OSError:
        return None


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = argparse.ArgumentParser(prog="readtheroom")
    p.add_argument("--simulate", action="store_true",
                   help="no camera: drive gaze from the nurse page's test buttons")
    p.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    p.add_argument("--frame-file", help="read frames from a JPEG written by a capture process")
    p.add_argument("--model", default=os.path.join(root, "models", "face_landmarker.task"))
    p.add_argument("--zones", default=os.path.join(root, "config", "zones.json"))
    p.add_argument("--data-dir", default=os.path.join(root, "data"))
    p.add_argument("--bed", default="Bed 4")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    cfg = Config(bed=args.bed)
    zones = load_zones(args.zones)
    source = extractor = sensor = None
    if not args.simulate:
        from .camera import FileFrameSource, OpenCVCamera
        from .features import FaceFeatureExtractor
        from .sensor import open_sensor

        if not os.path.exists(args.model):
            raise SystemExit(f"Face model not found at {args.model}. See README.")
        extractor = FaceFeatureExtractor(args.model, cfg.blink_threshold)
        source = FileFrameSource(args.frame_file) if args.frame_file else OpenCVCamera(args.camera)
        sensor = open_sensor()

    hub = Hub()
    app = App(cfg, zones, args.data_dir, hub, source, extractor, sensor, args.simulate)
    serve(hub, app, args.host, args.port)
    print(f"Patient screen: http://localhost:{args.port}/")
    print(f"Nurse station:  http://localhost:{args.port}/nurse")
    address = _lan_address()
    if address and args.host == "0.0.0.0":
        print(f"From other devices on this network: http://{address}:{args.port}/ "
              f"and http://{address}:{args.port}/nurse")
    if sensor is None and not args.simulate:
        print("No temperature sensor found; room-climate prompts are off.")
    try:
        app.run()
    except KeyboardInterrupt:
        pass
