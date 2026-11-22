from __future__ import annotations

"""One photo of the table, taken once, and the spots on it the patient can
look at. Nothing here runs live: the photo is a map, the targets are points
on that map, and gazeray.py decides which point a look is on.

A target is a box on the photo plus the need it stands for (question and
message from the needs table in zones.json). The screen itself is a target
too, under the id REST, so the device knows what "looking at the screen"
looks like and can read YES and NO beside it.
"""

import itertools
import json
import math
import os
import re
import struct

from .config import REST
from .prompts import Zone

SCREEN_WORDS = ("laptop", "monitor", "screen")


def jpeg_size(data: bytes) -> tuple | None:
    """(width, height) from a JPEG's header, without decoding it."""
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    return None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "spot"


def overlap(a: list, b: list) -> float:
    """Share of the two boxes' union that both cover (intersection over union)."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def spread_out(items: list, keep: int) -> set:
    """Indices of the `keep` items whose closest pair is furthest apart.
    Items are (x, y); a bird's-eye photo makes photo distance a fair stand-in
    for the angle between two objects as seen from the bed."""
    if len(items) <= keep:
        return set(range(len(items)))
    best, best_gap = None, -1.0
    for combo in itertools.combinations(range(len(items)), keep):
        gap = min(math.dist(items[a], items[b]) for a, b in itertools.combinations(combo, 2))
        if gap > best_gap:
            best, best_gap = combo, gap
    return set(best)


class Table:
    def __init__(self, stem: str, needs: dict, max_active: int = 3):
        self.max_active = max_active   # objects calibrated in one session
        self._stem = stem                 # data/table -> table.jpg and table.json
        self.needs = needs
        self.photo: bytes | None = None
        self.photo_id = 0
        self.size: tuple | None = None
        self.found: list = []             # what the detector saw, for suggestions
        self.detect_error: str | None = None
        self.targets: list = []           # [{id, label, box, question, message, urgent}]
        self.version = 0
        self._load()

    # --- the photo --------------------------------------------------------
    def set_photo(self, jpeg: bytes, detector=None) -> list:
        size = jpeg_size(jpeg)
        if size is None:
            raise ValueError("not a JPEG photo")
        self.photo, self.size = jpeg, size
        self.photo_id += 1
        self.found, self.detect_error = [], None
        if detector is not None:
            try:
                self.found = self._detect(jpeg, detector)
            except Exception as exc:  # a detector that cannot run on this machine: spots are drawn by hand
                self.detect_error = f"{type(exc).__name__}: {str(exc).splitlines()[-1][:160]}"
                print(f"Object detection failed on the table photo; mark the spots by hand. {self.detect_error}", flush=True)
        self.targets = []  # a new photo means new positions
        self.version += 1
        self._save()
        return self.suggestions()

    def _detect(self, jpeg: bytes, detector) -> list:
        return [{"label": d["label"], "score": round(float(d["score"]), 3),
                 "box": [round(float(v), 1) for v in d["box"]]}
                for d in detector.detect_jpeg(jpeg)]

    def suggestions(self) -> list:
        """Targets the detector proposes: every known need it saw, plus the
        screen. The nurse confirms or edits them on the caregiver page.

        One object, one spot: the detector often reports the same thing
        under two names (a bottle that is also a "cup"), and two spots on
        one object can never be told apart by a look. The better-scoring
        name keeps the box; anything mostly inside a box already taken is
        dropped."""
        out, used = [], set()
        for d in sorted(self.found, key=lambda d: -d["score"]):
            label = d["label"]
            if any(overlap(d["box"], o["box"]) > 0.5 for o in out):
                continue
            if label in SCREEN_WORDS and REST not in used:
                out.append({"id": REST, "label": "screen", "box": d["box"]})
                used.add(REST)
            elif label in self.needs and label not in used:
                out.append({"id": "t:" + slug(label), "label": label, "box": d["box"],
                            "need": label})
                used.add(label)
        return out

    # --- the targets ------------------------------------------------------
    def set_targets(self, targets: list) -> None:
        if self.size is None:
            raise ValueError("photograph the table first")
        w, h = self.size
        clean, ids = [], set()
        for t in targets:
            box = [float(v) for v in t["box"]]
            if len(box) != 4 or not (0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h):
                raise ValueError("a box is outside the photo")
            if t.get("id") == REST:
                item = {"id": REST, "label": "screen", "box": box}
            else:
                need = self.needs.get(t.get("need") or t.get("label"))
                if need is None:
                    raise ValueError(f"no question for {t.get('need') or t.get('label')!r}")
                label = str(t.get("label") or t["need"])
                item = {"id": "t:" + slug(label), "label": label, "box": box,
                        "need": t.get("need") or label,
                        "question": need["question"], "message": need["message"],
                        "urgent": bool(need.get("urgent", False))}
            if item["id"] in ids:
                raise ValueError(f"two targets called {item['label']!r}")
            ids.add(item["id"])
            if "active" in t:
                item["active"] = bool(t["active"])
            clean.append(item)
        # Objects without a say get chosen: the most spread-out few are in.
        objects = [t for t in clean if t["id"] != REST]
        if any("active" not in t for t in objects):
            chosen = spread_out([((t["box"][0] + t["box"][2]) / 2, (t["box"][1] + t["box"][3]) / 2) for t in objects],
                                self.max_active)
            for i, t in enumerate(objects):
                t["active"] = i in chosen
        for t in clean:
            if t["id"] == REST:
                t["active"] = True
        self.targets = clean
        self.version += 1
        self._save()

    def zones(self) -> dict:
        """What the session can ask about: every target except the screen."""
        return {t["id"]: Zone(t["id"], t["label"], t["question"], t["message"], t["urgent"])
                for t in self.targets if t["id"] != REST and t.get("active", True)}

    def has_screen(self) -> bool:
        return any(t["id"] == REST for t in self.targets)

    def centres(self) -> dict:
        """Target id -> box centre as fractions of the photo, for the live dot."""
        if self.size is None:
            return {}
        w, h = self.size
        return {t["id"]: ((t["box"][0] + t["box"][2]) / 2 / w, (t["box"][1] + t["box"][3]) / 2 / h)
                for t in self.targets}

    def snapshot(self) -> dict:
        return {"photo_id": self.photo_id if self.photo else None, "size": self.size,
                "found": self.found, "detect_error": self.detect_error, "targets": self.targets}

    # --- on disk ----------------------------------------------------------
    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._stem) or ".", exist_ok=True)
        if self.photo:
            with open(self._stem + ".jpg", "wb") as f:
                f.write(self.photo)
        with open(self._stem + ".json", "w", encoding="utf-8") as f:
            json.dump({"size": self.size, "found": self.found, "targets": self.targets}, f)

    def _load(self) -> None:
        try:
            with open(self._stem + ".jpg", "rb") as f:
                self.photo = f.read()
            with open(self._stem + ".json", encoding="utf-8") as f:
                data = json.load(f)
            self.size = tuple(data["size"]) if data.get("size") else jpeg_size(self.photo)
            self.found = list(data.get("found", []))
            self.targets = list(data.get("targets", []))
            self.photo_id = 1
        except (OSError, ValueError, KeyError, TypeError):
            self.photo, self.size, self.found, self.targets = None, None, [], []
