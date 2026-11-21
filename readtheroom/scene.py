from __future__ import annotations

from dataclasses import replace

from .prompts import Zone


def overlap(a, b) -> float:
    """Intersection over union of two [x1, y1, x2, y2] boxes."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class SceneTracker:
    """Keeps each gaze spot's label in step with what is actually there.

    Each zone names the object expected at its spot (`detect`). The first
    time that object is seen, its place in the scene photo becomes the spot.
    If a different known object later sits in that place for `confirm`
    photos in a row, the zone asks about that object instead. If the
    detector simply misses the object, nothing changes.
    """

    def __init__(self, zones: dict, needs: dict, min_overlap: float = 0.2, confirm: int = 2):
        self.zones = {zid: z for zid, z in zones.items() if z.detect}
        self.needs = needs
        self.min_overlap = min_overlap
        self.confirm = confirm
        self.reset()

    def reset(self) -> None:
        self._spot: dict = {}      # zone id -> box in the scene photo
        self._current: dict = {}   # zone id -> word for what is there now
        self._seen: dict = {}      # zone id -> something recognised there in the last photo
        self._pending: dict = {}   # zone id -> (word, photos in a row)

    def update(self, detections: list, size=None) -> None:
        for zid, zone in self.zones.items():
            if zid not in self._spot:
                expected = [d for d in detections if d["label"] == zone.detect]
                if expected:
                    self._spot[zid] = max(expected, key=lambda d: d["score"])["box"]
                    self._current[zid] = zone.detect
                    self._seen[zid] = True
                continue
            here = [d for d in detections
                    if overlap(self._spot[zid], d["box"]) >= self.min_overlap
                    and (d["label"] == zone.detect or d["label"] in self.needs)]
            self._seen[zid] = bool(here)
            if not here:
                self._pending.pop(zid, None)
                continue
            # Prefer what is already believed to be there, then the best score.
            best = max(here, key=lambda d: (d["label"] == self._current[zid], d["score"]))
            if best["label"] == self._current[zid]:
                self._pending.pop(zid, None)
                self._spot[zid] = best["box"]  # follow small nudges
                continue
            word, count = self._pending.get(zid, (best["label"], 0))
            count = count + 1 if word == best["label"] else 1
            if count >= self.confirm:
                self._current[zid] = best["label"]
                self._spot[zid] = best["box"]
                self._pending.pop(zid, None)
            else:
                self._pending[zid] = (best["label"], count)

    def overrides(self) -> dict:
        """Zones whose spot now holds a different object: zone id -> Zone to use."""
        out = {}
        for zid, word in self._current.items():
            zone = self.zones[zid]
            if word == zone.detect or word not in self.needs:
                continue
            need = self.needs[word]
            out[zid] = replace(zone, label=word, question=need["question"],
                               message=need["message"],
                               urgent=bool(need.get("urgent", False)), climate=False)
        return out

    def status(self) -> list:
        return [{"zone": zid, "expected": zone.detect,
                 "located": zid in self._spot,
                 "box": self._spot.get(zid),
                 "now": self._current.get(zid),
                 "seen": self._seen.get(zid, False)}
                for zid, zone in self.zones.items()]


SCREEN_WORDS = ("laptop", "monitor", "screen")


class SceneMap:
    """Where each recognised object lies relative to the patient screen.

    The scene camera looks the way the patient looks, so an object left of
    the screen in its picture is to the patient's left. Gaze only gives a
    direction; this map says what is actually in that direction, so the
    device can ask about it. Nothing is typed in: objects are whatever the
    detector finds that the needs table has a question for.

    Objects close to the screen's own line of sight cannot be placed left or
    right and are listed as unplaced. An object stays on the map for `hold`
    photos after it was last seen, so one missed detection changes nothing.
    """

    def __init__(self, needs: dict, hold: int = 3, margin: float = 0.08):
        self.needs = needs
        self.hold = hold
        self.margin = margin
        self.reset()

    def reset(self) -> None:
        self._objects: dict = {}   # (label, side) -> {box, score, age, dx, dy}
        self._unplaced: dict = {}  # label -> age
        self._screen = None        # box of the patient screen in the photo
        self._screen_age = 0

    def update(self, detections: list, size=None) -> None:
        if not size:
            return
        w, h = size
        screens = [d for d in detections if d["label"] in SCREEN_WORDS]
        if screens:
            self._screen = max(screens, key=lambda d: d["score"])["box"]
            self._screen_age = 0
        elif self._screen is not None:
            self._screen_age += 1
            if self._screen_age > self.hold * 3:
                self._screen = None
        if self._screen is not None:
            cx = (self._screen[0] + self._screen[2]) / 2
            cy = (self._screen[1] + self._screen[3]) / 2
            half = (self._screen[2] - self._screen[0]) / 2
        else:  # setup rule: the scene camera is aimed at the screen
            cx, cy, half = w / 2, h / 2, 0.0
        limit = max(self.margin * w, 0.6 * half)

        for entry in self._objects.values():
            entry["age"] += 1
        for label in self._unplaced:
            self._unplaced[label] += 1
        for d in detections:
            if d["label"] not in self.needs:
                continue
            ox, oy = (d["box"][0] + d["box"][2]) / 2, (d["box"][1] + d["box"][3]) / 2
            dx = ox - cx
            if abs(dx) < limit:
                self._unplaced[d["label"]] = 0
                continue
            key = (d["label"], "left" if dx < 0 else "right")
            old = self._objects.get(key)
            if old is None or old["age"] > 0 or d["score"] > old["score"]:
                self._objects[key] = {"box": d["box"], "score": d["score"], "age": 0,
                                      "dx": abs(dx), "dy": abs(oy - cy)}
        self._objects = {k: v for k, v in self._objects.items() if v["age"] <= self.hold}
        self._unplaced = {k: v for k, v in self._unplaced.items() if v <= self.hold}

    @staticmethod
    def zone_id(label: str, side: str) -> str:
        return f"obj:{side}:{label}"

    def sides(self) -> dict:
        """{'left': [...], 'right': [...]}, each nearest the screen's height first."""
        out = {"left": [], "right": []}
        for (label, side), entry in self._objects.items():
            out[side].append({"id": self.zone_id(label, side), "label": label,
                              "box": entry["box"], "order": (entry["dy"], entry["dx"])})
        for items in out.values():
            items.sort(key=lambda item: item["order"])
        return out

    def overrides(self) -> dict:
        return {}

    def status(self) -> list:
        rows = [{"zone": self.zone_id(label, side), "expected": label, "located": True,
                 "box": entry["box"], "now": label, "seen": entry["age"] == 0, "side": side}
                for (label, side), entry in sorted(self._objects.items(), key=lambda kv: kv[0][1])]
        rows += [{"zone": None, "expected": label, "located": False, "box": None,
                  "now": label, "seen": age == 0, "side": None}
                 for label, age in self._unplaced.items()
                 if not any(k[0] == label for k in self._objects)]
        return rows

    @property
    def screen(self):
        return self._screen


class SceneWorker:
    """Runs the object detector on scene photos in its own thread, so a slow
    detection never delays reading the patient's eyes. Only the newest photo
    waiting is kept."""

    def __init__(self, detector, tracker: SceneTracker, mono=None):
        import threading
        import time

        self._detector = detector
        self._tracker = tracker
        self._mono = mono or time.monotonic
        self._cond = threading.Condition()
        self._waiting: bytes | None = None
        self._lock = threading.Lock()
        self.version = 0
        self._found: list = []
        self._at: float | None = None
        self._ms: float | None = None
        self._error: str | None = None
        self._photo: bytes | None = None   # last photo that was read successfully
        self._photo_id = 0
        self._size = None                  # (width, height) of that photo
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, jpeg: bytes) -> None:
        with self._cond:
            self._waiting = jpeg
            self._cond.notify()

    def reset(self) -> None:
        with self._lock:
            self._tracker.reset()
            self.version += 1

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._waiting is None:
                    self._cond.wait()
                jpeg, self._waiting = self._waiting, None
            self.process(jpeg)

    def process(self, jpeg: bytes) -> None:
        import cv2
        import numpy as np

        started = self._mono()
        found, error, size = [], None, None
        try:
            frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                error = "The scene photo could not be read."
            else:
                size = (int(frame.shape[1]), int(frame.shape[0]))
                found = self._detector.detect(frame)
        except Exception as exc:  # a bad photo must not stop the worker
            error = f"Object detection failed: {exc}"
        with self._lock:
            if error is None:
                self._tracker.update(found, size)
                self._found = found
                self._photo, self._size = jpeg, size
                self._photo_id += 1
            self._error = error
            self._at = self._mono()
            self._ms = round((self._at - started) * 1000)
            self.version += 1

    def overrides(self) -> dict:
        with self._lock:
            return self._tracker.overrides()

    def sides(self) -> dict:
        """Objects to the patient's left and right (SceneMap only; else empty)."""
        with self._lock:
            return self._tracker.sides() if hasattr(self._tracker, "sides") else {}

    def photo(self):
        """The last scene photo, for the room view. It shows the objects, not the patient."""
        with self._lock:
            return self._photo

    def snapshot(self) -> dict:
        with self._lock:
            return {"age_s": None if self._at is None else round(self._mono() - self._at, 1),
                    "ms": self._ms, "error": self._error,
                    "photo_id": self._photo_id, "size": self._size,
                    "found": [{"label": d["label"], "score": d["score"], "box": d["box"]}
                              for d in self._found],
                    "screen": getattr(self._tracker, "screen", None),
                    "spots": self._tracker.status()}
