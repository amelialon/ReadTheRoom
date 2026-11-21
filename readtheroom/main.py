from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import traceback

from .alerts import AlertStore
from .calibration import Calibrator, control_targets
from .config import ANSWER_MODES, Config, NO, REST, YES
from .directions import CENTRE, LEFT, RIGHT, UP, DirectionReader
from .features import Ema
from .gazeray import GazeRay
from .prompts import Zone, load_menu, load_needs, load_zones
from .server import Hub, serve
from .session import Session
from .table import Table
from .zones import ZoneClassifier


class App:
    def __init__(self, cfg: Config, zones: dict, data_dir: str, hub: Hub,
                 source=None, extractor=None, sensor=None, simulate: bool = False,
                 scene=None, menu=(), needs=None, detector=None):
        self.cfg = cfg
        self.zones = zones
        self.hub = hub
        self.source = source
        self.extractor = extractor
        self.sensor = sensor
        self.simulate = simulate
        self.scene = scene  # SceneWorker, or None when object detection is off
        self._scene_version = 0
        os.makedirs(data_dir, exist_ok=True)
        # Each answer mode keeps its own file: the same target names mean
        # different places on the screen.
        name = "calibration.json" if cfg.answer_mode == "horizontal" else "calibration-vertical.json"
        self._calibration_path = os.path.join(data_dir, name)
        self.alerts = AlertStore(cfg.escalate_after_s, os.path.join(data_dir, "alerts.json"))
        self.session = Session(cfg, zones, self.alerts, menu)
        self.classifier = ZoneClassifier()
        self.self_check: dict = {}
        targets = control_targets(cfg.answer_mode)
        targets += [(z.id, f"Look at the {z.label}") for z in zones.values()]
        self._expected = {t[0] for t in targets}
        self.calibrator = Calibrator(targets, cfg.settle_s, cfg.record_s)
        # Quick mode needs no guided routine: one look at the centre of the
        # screen, then left / right / up are read as directions.
        # Up/down answers need the guided routine, so quick mode is left/right only.
        self.quick = cfg.gaze_mode == "quick" and cfg.answer_mode == "horizontal"
        if self.quick:
            self.reader = DirectionReader(cfg.dir_h_threshold, cfg.dir_v_threshold, cfg.dir_adapt,
                                          iris_weight=cfg.dir_iris_weight, head_weight=cfg.dir_head_weight,
                                          iris_v_weight=cfg.dir_iris_v_weight,
                                          head_v_weight=cfg.dir_head_v_weight)
            self._slots = {z.slot: z.id for z in zones.values() if z.slot}
            # Direction -> zone ids to ask about, in order. Filled from the
            # scene camera when it sees objects; the typed slots otherwise.
            self._sides = {side: [zid] for side, zid in self._slots.items()}
            self._needs = needs or {}
            self.calibrator = Calibrator([(REST, "Hello. Look here for a moment.")],
                                         cfg.greet_settle_s, cfg.greet_record_s, passes=1)
            self._greet_after = 0.0
            self._q_state = None
            self._q_centred = False
        # Table mode: one photo of the table is the map, each object on it is a
        # point in the room learned from one look, and the gaze is a ray.
        self.table_mode = cfg.gaze_mode == "table" and cfg.answer_mode == "horizontal"
        if self.table_mode:
            self.quick = False
            self.ray = GazeRay(cfg.ray_eye_gain, cfg.ray_eye_clamp, cfg.ray_iris_gain,
                               cfg.ray_distance_cm, cfg.ray_accept_deg, cfg.ray_margin_deg)
            self._ray_path = os.path.join(data_dir, "ray.json")
            self.table = Table(os.path.join(data_dir, "table"), needs or {})
            self.detector = detector
            self._needs = needs or {}
            self._want_teach: str | None = None
            self._teaching: str | None = None
            self._table_dirty = False
            self._feature = None
            self._q_state = None
            self._q_centred = False
            self.calibrator = Calibrator([(REST, "Look at the screen")],
                                         cfg.teach_settle_s, cfg.teach_record_s, passes=1)
        self._want_calibration = False
        self._debug_gaze: str | None = REST
        self._temp: float | None = None
        self._humidity: float | None = None
        self._fps = 0.0
        self._ema = Ema(cfg.ema_alpha)
        self._last = (None, False)
        self._last_frame_at: float | None = None
        self._frame = None
        self._gaze_ok = False
        self._preview_until = 0.0
        self._preview_cache = (None, None)
        self._last_log = None
        self._load_calibration()

    def _load_calibration(self) -> None:
        if self.table_mode:
            if os.path.exists(self._ray_path):
                try:
                    self.ray.load(self._ray_path)
                except (OSError, ValueError, KeyError, TypeError):
                    self.ray.forget()
            self._apply_table()
            return
        if self.simulate or self.quick or not os.path.exists(self._calibration_path):
            return
        try:
            self.classifier.load(self._calibration_path)
        except Exception:  # any unreadable file just means "calibrate again"
            self.classifier.fitted = False
            return
        if self.classifier.labels != self._expected:  # zones file changed
            self.classifier.fitted = False

    # Called from web-server threads.
    def set_table_photo(self, jpeg: bytes) -> dict:
        if not self.table_mode:
            raise ValueError("not in table mode")
        suggested = self.table.set_photo(jpeg, self.detector)
        self.ray.forget()
        self.self_check = {}
        self._table_dirty = True
        return {"found": self.table.found, "suggested": suggested}

    def set_table_targets(self, targets: list) -> None:
        if not self.table_mode:
            raise ValueError("not in table mode")
        self.table.set_targets(targets)
        self._table_dirty = True

    def request_teach(self, target: str) -> bool:
        if not self.table_mode or self.simulate:
            return False
        if target not in {t["id"] for t in self.table.targets}:
            return False
        self._want_teach = target
        return True

    def forget_target(self, target: str | None) -> bool:
        if not self.table_mode:
            return False
        self.ray.forget(target)
        if target is None:
            self.self_check = {}
        else:
            self.self_check.pop(target, None)
        try:
            self.ray.save(self._ray_path)
        except OSError:
            pass
        return True

    def table_photo(self):
        return self.table.photo if self.table_mode else None

    def snap_table_photo(self) -> dict:
        """Use the patient camera's current picture as the table photo: the
        nurse turns the camera to the table for a moment."""
        if not self.table_mode or self.simulate:
            raise ValueError("no camera to photograph with")
        frame = self._frame
        if frame is None:
            raise ValueError("no picture from the camera yet")
        import cv2
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError("could not encode the picture")
        return self.set_table_photo(jpeg.tobytes())

    def _apply_table(self) -> None:
        """The targets on the photo become what can be asked about. With no
        photo yet, the zones file stands in."""
        zones = self.table.zones() if self.table.photo else self.zones
        self.session.set_zones(zones)
        keep = {t["id"] for t in self.table.targets}
        for target in list(self.ray.points):
            if target not in keep:
                self.ray.forget(target)
                self.self_check.pop(target, None)

    def request_calibration(self) -> bool:
        if self.simulate:
            return False
        self._want_calibration = True
        return True

    def submit_scene(self, jpeg: bytes) -> bool:
        if self.scene is None:
            return False
        self.scene.submit(jpeg)
        return True

    def reset_scene(self) -> bool:
        if self.scene is None:
            return False
        self.scene.reset()
        return True

    def set_preview(self, on: bool) -> bool:
        """The camera view is for positioning during setup only. It is off by
        default and switches itself off, so no video leaves the device in use."""
        if self.simulate:
            return False
        self._preview_until = time.monotonic() + self.cfg.preview_s if on else 0.0
        return True

    def _preview_on(self) -> bool:
        return time.monotonic() < self._preview_until

    def _face_width(self):
        box = getattr(self.extractor, "last_box", None)
        return None if box is None else round(box[2] - box[0], 3)

    def preview_jpeg(self):
        """Small mirrored picture of what the camera sees. Nothing is drawn
        on it: the page adds a soft frame around the face. None when the
        preview is off or there is no picture. The same frame always gives
        the same object back, so a stream can tell whether it is new."""
        frame = self._frame
        if frame is None or not self._preview_on():
            return None
        if self._preview_cache[0] is frame:
            return self._preview_cache[1]
        import cv2

        h, w = frame.shape[:2]
        scale = 480 / w
        img = cv2.resize(frame, (480, round(h * scale)))
        img = cv2.flip(img, 1)
        ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
        result = jpeg.tobytes() if ok else None
        self._preview_cache = (frame, result)
        return result

    def _face_box(self):
        """Face position in the mirrored preview, as fractions of the picture."""
        box = getattr(self.extractor, "last_box", None)
        if box is None or not self._preview_on():
            return None
        return [round(v, 4) for v in (1 - box[2], box[1], 1 - box[0], box[3])]

    def scene_photo(self):
        return None if self.scene is None else self.scene.photo()

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
        self._frame = frame
        face, feature = self.extractor(frame, int(now * 1000))
        self._gaze_ok = feature is not None
        feature = self._ema(feature)
        if self.calibrator.active:
            if self.calibrator.update(now, feature):
                self._finish_calibration(now)
            return None, face
        if self.quick:
            return self._perceive_quick(now, feature), face
        if self.table_mode:
            return self._perceive_table(now, feature), face
        return self.classifier.predict(feature), face

    def _perceive_table(self, now: float, feature):
        """Which learned point the gaze ray is on. As in quick mode the eyes
        must come back to the screen after every change of screen before a
        look counts, so the look that raised a question cannot answer it."""
        self._feature = feature
        label = self.ray.predict(feature)
        state = self.session.state
        if state != self._q_state:
            self._q_state, self._q_centred = state, False
        if feature is None:
            return None
        if label == REST:
            self._q_centred = True
            return REST
        if not self._q_centred:
            return None
        off = self.ray.offset_from(REST, feature)  # (left, up) in degrees from the screen
        answer = self.cfg.ray_answer_deg
        if state == "idle":
            if label is not None and label in self.session.zones:
                return label
            # A look well above the screen, at nothing learned, opens the menu.
            if off is not None and off[1] >= 2 * answer and abs(off[0]) < 2 * answer:
                return NO
            return None
        if state in ("prompt", "menu") and off is not None:
            if off[0] >= answer:
                return YES
            if off[0] <= -answer:
                return NO
        return None

    def _perceive_quick(self, now: float, feature):
        """Direction -> the label the session understands. What a direction
        means depends on the moment: with nothing asked, left and right are
        the objects placed there and up opens the menu; once a question or
        menu item is showing, left is YES and right is NO."""
        if self.reader.centre is None:
            if feature is not None and now >= self._greet_after:
                self.calibrator.start(now)  # the greeting: learn "centre"
            return None
        direction = self.reader.read(feature)
        state = self.session.state
        if state != self._q_state:
            # The screen just changed. Eyes must come back to the centre
            # before a direction counts, so the look that raised a question
            # can never also answer it.
            self._q_state, self._q_centred = state, False
        if direction is None:
            return None
        if direction == CENTRE:
            self._q_centred = True
            if state == "idle":
                self.reader.adapt(feature)
            return REST
        if not self._q_centred:
            return None
        if state == "idle":
            # Session opens the menu on a long hold of NO while idle; in quick
            # mode that hold is "up", since right is an object.
            if direction == UP:
                return NO
            # Several objects may lie the same way: ask about the first one
            # the patient has not just said NO to.
            for zone_id in self._sides.get(direction, ()):
                if not self.session.is_backed_off(zone_id, now):
                    return zone_id
            return None
        if state in ("prompt", "menu"):
            return {LEFT: YES, RIGHT: NO}.get(direction)
        return None

    def _apply_scene_map(self) -> None:
        """Let the scene decide what is to the left and right. With nothing
        recognised, fall back to the objects typed into the zones file."""
        sides = self.scene.sides()
        if not any(sides.values()):
            self._sides = {side: [zid] for side, zid in self._slots.items()}
            self.session.set_zones(self.zones)
            return
        zones, ids = {}, {}
        for side, items in sides.items():
            ids[side] = []
            for item in items:
                need = self._needs[item["label"]]
                zones[item["id"]] = Zone(item["id"], item["label"], need["question"],
                                         need["message"], bool(need.get("urgent", False)))
                ids[side].append(item["id"])
        self._sides = ids
        self.session.set_zones(zones)

    def _log_changes(self, now: float, label) -> None:
        """One line per change of state on the console: time, what happened,
        and what the gaze read as. Enough to trace any false alarm."""
        ses = self.session
        key = (ses.state, ses.prompt["question"] if ses.prompt else None,
               ses.menu_index if ses.state == "menu" else None, len(ses.alerts.list(0)))
        if key == self._last_log:
            return
        self._last_log = key
        stamp = time.strftime("%H:%M:%S")
        if ses.state == "prompt":
            what = f"asking: {ses.prompt['question']}"
        elif ses.state == "menu":
            what = f"menu: {ses.menu[ses.menu_index].label}"
        elif ses.state == "sent":
            what = f"SENT: {ses.alerts.list()[0]['message']}"
        else:
            what = "idle"
        print(f"[{stamp}] {what}   (gaze read as: {label})", flush=True)

    def _finish_calibration(self, now: float) -> None:
        self.session.reset(now)
        if self.table_mode:
            target, self._teaching = self._teaching, None
            if self.calibrator.error or target is None:
                return
            try:
                frames = self.calibrator.X
                distance = None
                if target == REST:
                    # The screen is by the camera, so it is as far as the head is.
                    origins = [r[0] for r in (self.ray.ray(f) for f in frames) if r is not None]
                    if origins:
                        distance = float(sum(float((o ** 2).sum()) ** 0.5 for o in origins) / len(origins))
                self.ray.learn(target, frames, distance)
                self.self_check[target] = self.ray.self_check({target: frames})[target]
                self.ray.save(self._ray_path)
            except (ValueError, OSError) as exc:
                self.calibrator.error = f"Could not learn that look: {exc}"
            return
        if self.quick:
            if self.calibrator.error:
                self._greet_after = now + self.cfg.greet_retry_s  # try again shortly
            else:
                self.reader.set_centre(self.calibrator.X)
                self._q_state = None
            return
        if self.calibrator.error:
            return  # any earlier calibration stays in use
        try:
            cal = self.calibrator
            first = [i for i, p in enumerate(cal.passes) if p == 0]
            second = [i for i, p in enumerate(cal.passes) if p != 0]
            # Learn from the first look at each target, test on the second
            # look, then keep everything.
            self.classifier.fit([cal.X[i] for i in first], [cal.y[i] for i in first])
            self.self_check = self.classifier.score([cal.X[i] for i in second],
                                                    [cal.y[i] for i in second])
            self.classifier.fit(cal.X, cal.y)
            self.classifier.save(self._calibration_path)
        except (ValueError, OSError) as exc:
            self.calibrator.error = f"Calibration failed: {exc}"

    def tick(self, now: float) -> None:
        started = time.monotonic()
        if self._want_calibration:
            self._want_calibration = False
            self.calibrator.start(now)
        if self.table_mode:
            if self._table_dirty:
                self._table_dirty = False
                self._apply_table()
            if self._want_teach and not self.calibrator.active:
                target, self._want_teach = self._want_teach, None
                label = next((t["label"] for t in self.table.targets if t["id"] == target), target)
                what = "the screen" if target == REST else "the " + label
                self.calibrator = Calibrator([(target, f"Look at {what}")], self.cfg.teach_settle_s,
                                             self.cfg.teach_record_s, passes=1)
                self._teaching = target
                self.calibrator.start(now)
        if self.scene is not None and self.scene.version != self._scene_version:
            self._scene_version = self.scene.version
            if self.quick:
                self._apply_scene_map()
            else:
                self.session.set_overrides(self.scene.overrides())
        label, face = self._perceive(now)
        self._last = (label, face)
        if self.table_mode:
            ready = self.simulate or (REST in self.ray.points
                                      and any(z in self.ray.points for z in self.session.zones))
        else:
            ready = self.simulate or (self.reader.centre is not None if self.quick
                                      else self.classifier.fitted)
        if self.calibrator.active or not ready:
            self.session.mark_face(now, face)
        else:
            self.session.update(now, label, face, self._temp)
        self.alerts.tick()
        self._log_changes(now, label)
        lost = self.session.face_lost_s(now)
        self.hub.publish({
            "bed": self.cfg.bed,
            "time": time.time(),
            "zones": [{"id": zid, "label": self.session.zone(zid).label}
                      for zid in self.session.zones],
            "scene": None if self.scene is None else self.scene.snapshot(),
            "table": self._table_snapshot(ready) if self.table_mode else None,
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
                "dropped": getattr(self.source, "dropped", None),
                "too_slow": 0 < self._fps < self.cfg.min_fps,
                "nurse_screens": self.hub.count("nurse"),
                "simulate": self.simulate,
                "answer_mode": self.cfg.answer_mode,
                "gaze_mode": "simulate" if self.simulate else ("quick" if self.quick else "table" if self.table_mode else "trained"),
                "sides": self._sides if self.quick and not self.simulate else None,
                "gaze_meter": self.reader.last() if self.quick and not self.simulate else None,
                "preview": self._preview_on(),
                "face_width": self._face_width(),
                "face_box": self._face_box(),
                "brightness": None if self._frame is None else round(float(self._frame[::8, ::8].mean())),
                "gaze_ok": self._gaze_ok,
            },
        })

    def _table_snapshot(self, ready: bool) -> dict:
        snap = self.table.snapshot()
        snap.update({
            "needs": sorted(self._needs),
            "learned": {t: self.ray.spread.get(t) for t in self.ray.points},
            "errors": self.ray.last(),
            "gaze_uv": None if self.simulate else self.ray.project(self._feature, self.table.centres()),
            "teaching": self._teaching,
            "ready": ready,
        })
        return snap

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


def _open_detector(model_path: str):
    """The object detector for the table photo, or None (the nurse then
    draws the spots by hand)."""
    if not os.path.exists(model_path):
        print("No object detector model; spots on the table photo are drawn by hand.")
        return None
    try:
        from .detector import SceneDetector
        detector = SceneDetector(model_path)
    except Exception as exc:
        print(f"Object detection is off: {exc}")
        return None
    print(f"Object detection is on ({detector.backend}) for the table photo.")
    return detector


def _open_scene(model_path: str, zones: dict, needs: dict, quick: bool = False):
    """Object detection for scene photos, or None if it cannot be used."""
    if not os.path.exists(model_path):
        print("No object detector model; spots keep their typed labels.")
        return None
    try:
        from .detector import SceneDetector
        from .scene import SCREEN_WORDS, SceneMap, SceneTracker, SceneWorker

        detector = SceneDetector(model_path)
    except Exception as exc:
        print(f"Object detection is off: {exc}")
        return None
    wanted = {z.detect for z in zones.values() if z.detect} | set(needs)
    unknown = sorted(wanted - set(detector.words))
    if unknown:
        print("The detector model was not exported with these words, so it can "
              f"never find them: {', '.join(unknown)}. Re-run tools/export_yoloworld.py.")
    print(f"Object detection is on ({detector.backend}). Send scene photos with: "
          "python -m readtheroom.scene_sender")
    if quick:
        if not set(SCREEN_WORDS) & set(detector.words):
            print("The detector cannot recognise the patient screen, so the scene "
                  "camera must be aimed with the screen in the middle of its picture.")
        return SceneWorker(detector, SceneMap(needs))
    return SceneWorker(detector, SceneTracker(zones, needs))


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
    p.add_argument("--gaze-mode", choices=("table", "quick", "trained"), default="table",
                   help="table: one photo of the table, one look per object (default); "
                        "quick: one look at the centre, objects left and right of the screen; "
                        "trained: the guided routine, objects anywhere")
    p.add_argument("--zones", default=os.path.join(root, "config", "zones.json"))
    p.add_argument("--scene-model", default=os.path.join(root, "models", "scene.onnx"),
                   help="object detector for scene photos; skipped if the file is missing")
    p.add_argument("--data-dir", default=os.path.join(root, "data"))
    p.add_argument("--bed", default="Bed 4")
    p.add_argument("--answer-mode", choices=ANSWER_MODES, default="horizontal",
                   help="vertical: YES is look up, NO is look down, for patients "
                        "who can only move their eyes up and down")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    gaze_mode = args.gaze_mode
    if gaze_mode == "quick" and args.answer_mode == "vertical":
        print("Up/down answers need the guided routine; using --gaze-mode trained.")
        gaze_mode = "trained"
    cfg = Config(bed=args.bed, answer_mode=args.answer_mode, gaze_mode=gaze_mode)
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

    needs = load_needs(args.zones)
    quick = cfg.gaze_mode == "quick" and cfg.answer_mode == "horizontal" and not args.simulate
    table = cfg.gaze_mode == "table" and cfg.answer_mode == "horizontal"
    scene = detector = None
    if table:
        detector = _open_detector(args.scene_model)
    else:
        scene = _open_scene(args.scene_model, zones, needs, quick)
    hub = Hub()
    app = App(cfg, zones, args.data_dir, hub, source, extractor, sensor, args.simulate, scene,
              load_menu(args.zones), needs=needs, detector=detector)
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
