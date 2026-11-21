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

    def update(self, detections: list) -> None:
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
                 "now": self._current.get(zid),
                 "seen": self._seen.get(zid, False)}
                for zid, zone in self.zones.items()]


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
        found, error = [], None
        try:
            frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                error = "The scene photo could not be read."
            else:
                found = self._detector.detect(frame)
        except Exception as exc:  # a bad photo must not stop the worker
            error = f"Object detection failed: {exc}"
        with self._lock:
            if error is None:
                self._tracker.update(found)
                self._found = found
            self._error = error
            self._at = self._mono()
            self._ms = round((self._at - started) * 1000)
            self.version += 1

    def overrides(self) -> dict:
        with self._lock:
            return self._tracker.overrides()

    def snapshot(self) -> dict:
        with self._lock:
            return {"age_s": None if self._at is None else round(self._mono() - self._at, 1),
                    "ms": self._ms, "error": self._error,
                    "found": [{"label": d["label"], "score": d["score"]} for d in self._found],
                    "spots": self._tracker.status()}
