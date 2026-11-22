"""Can gaze be mapped onto the scene photo well enough to pick an object?

    python3 -m readtheroom.check_mapping photo.jpg --frame-file /tmp/gaze.jpg

Finds the objects in a still photo from the scene camera, then asks for a
2-second look at each one, the screen centre, and the screen's left (YES)
and right (NO) edges. Fits a straight-line map from eye readings to photo
pixels, and tests it by leaving each target out of the fit in turn: the map
passes when every left-out target still lands nearest to itself. Then shows
where the gaze lands, live. Run it on the device, beside the app, so the
readings are the ones the app sees.
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np

from . import mapping
from .directions import signals
from .scene import SCREEN_WORDS

# Eye-only inputs: the patient may not be able to move their head. Simplest first,
# so a tie keeps the simpler fit.
FITS = [("affine", ("eye_h", "eye_v")),
        ("affine", ("eye_h", "eye_v", "iris_h", "iris_v")),
        ("homography", ("eye_h", "eye_v"))]


def targets_from(found: list, min_score: float) -> dict:
    """Target name -> box. Points on the screen are zero-size boxes. The
    screen's box includes the keyboard, so its middle is taken 40% down."""
    screens = [f for f in found if f["label"] in SCREEN_WORDS]
    if not screens:
        raise SystemExit("No laptop or monitor found in the photo: aim the scene camera at the screen.")
    x1, y1, x2, y2 = max(screens, key=lambda f: f["score"])["box"]
    y = y1 + 0.4 * (y2 - y1)
    edge = 0.12 * (x2 - x1)
    targets = {"screen centre": [(x1 + x2) / 2] * 2, "YES (screen left edge)": [x1 + edge] * 2,
               "NO (screen right edge)": [x2 - edge] * 2}
    targets = {n: [p[0], y, p[1], y] for n, p in targets.items()}
    for f in found:
        if f["label"] not in SCREEN_WORDS and f["score"] >= min_score and f["label"] not in targets:
            targets[f["label"]] = f["box"]
    return targets


class Eyes:
    """Newest gaze frame -> named eye readings, or None (no new frame, no face, eyes shut)."""

    def __init__(self, frame_file: str, face_model: str):
        from .camera import FileFrameSource
        from .features import FaceFeatureExtractor
        self._frames = FileFrameSource(frame_file)
        self._face = FaceFeatureExtractor(face_model)
        self._t0 = time.monotonic()

    def read(self):
        frame = self._frames.read()
        if frame is None:
            return None
        _, feature = self._face(frame, (time.monotonic() - self._t0) * 1000)
        return None if feature is None else signals(feature)


def record(eyes: Eyes, name: str, hold: float, settle: float) -> list:
    print(f"\nLook at: {name.upper()}")
    for n in (3, 2, 1):
        print(f"  {n}...", flush=True)
        time.sleep(1)
    print("  hold it", flush=True)
    rows, start = [], time.monotonic()
    while time.monotonic() - start < hold:
        s = eyes.read()
        if s is not None and time.monotonic() - start >= settle:
            rows.append(s)
        time.sleep(0.01)
    print(f"  got {len(rows)} frames" + ("  (too few: is the frame sender running?)" if len(rows) < 5 else ""))
    return rows


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = argparse.ArgumentParser(prog="readtheroom.check_mapping")
    p.add_argument("photo")
    p.add_argument("--frame-file", default="/tmp/gaze.jpg")
    p.add_argument("--model", default=os.path.join(root, "models", "scene.onnx"))
    p.add_argument("--face-model", default=os.path.join(root, "models", "face_landmarker.task"))
    p.add_argument("--min-score", type=float, default=0.4)
    p.add_argument("--hold", type=float, default=2.0)
    p.add_argument("--settle", type=float, default=0.7)
    p.add_argument("--live", type=float, default=60.0, help="seconds of live readout after the fit")
    p.add_argument("--out", default="mapping-check.json")
    args = p.parse_args()

    import cv2
    from .detector import SceneDetector

    photo = cv2.imread(args.photo)
    if photo is None:
        raise SystemExit(f"Cannot read image {args.photo}")
    size = (photo.shape[1], photo.shape[0])
    targets = targets_from(SceneDetector(args.model).detect(photo), args.min_score)
    print("Targets in the photo:", ", ".join(targets))

    eyes = Eyes(args.frame_file, args.face_model)
    rows = {name: record(eyes, name, args.hold, args.settle) for name in targets}
    rows = {n: r for n, r in rows.items() if len(r) >= 3}

    best = None
    for kind, keys in FITS:
        label = f"{kind} on {'+'.join(keys)}"
        samples = {n: np.array([[r[k] for k in keys] for r in rs]) for n, rs in rows.items()}
        try:
            report = mapping.leave_one_out(samples, targets, kind)
        except ValueError as e:
            print(f"\n{label}: skipped ({e})")
            continue
        ok = sum(r["nearest"] == n for n, r in report.items())
        err = float(np.median([r["error_px"] for r in report.values()]))
        print(f"\n{label}: {ok}/{len(report)} left-out targets land nearest to themselves, "
              f"median error {err:.0f} px")
        for n, r in report.items():
            print(f"  {n:<24} off by {r['error_px']:>6.0f} px   nearest: {r['nearest']}"
                  + ("" if r["nearest"] == n else "   <-- WRONG"))
        if best is None or (ok, -err) > (best[0], -best[1]):
            best = (ok, err, label, kind, keys, samples)
    if best is None:
        raise SystemExit("Too few targets were recorded to fit any map.")
    ok, err, label, kind, keys, samples = best
    passed = ok == len(samples)
    print(f"\n{'PASS' if passed else 'FAIL'}: best is {label}, {ok}/{len(samples)} targets placed")

    centres = {n: [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] for n, b in targets.items()}
    m = mapping.fit(samples, centres, kind)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"photo": args.photo, "size": size, "targets": targets, "inputs": list(keys),
                   "kind": kind, "map": m["m"].tolist(), "passed": passed,
                   "samples": {n: rows[n] for n in samples}}, f, indent=1)
    print(f"Saved readings and fit to {args.out}")

    objects = {n: b for n, b in targets.items() if n != "screen centre"}
    radius = mapping.reach(targets)
    print(f"\nLive for {args.live:.0f} s (Ctrl-C to stop). Reach: {radius:.0f} px around each target.")
    recent, shown = [], 0.0
    end = time.monotonic() + args.live
    try:
        while time.monotonic() < end:
            s = eyes.read()
            now = time.monotonic()
            if s is not None:
                recent.append((now, mapping.predict(m, np.array([s[k] for k in keys]))))
            recent = [(t, pt) for t, pt in recent if now - t <= 0.3]  # smooth over 0.3 s
            if recent and now - shown >= 0.3:
                shown = now
                pt = np.mean([pt for _, pt in recent], axis=0)
                hit = mapping.nearest(pt, objects, radius, size)
                print(f"  gaze at ({pt[0]:6.0f}, {pt[1]:6.0f})  ->  {hit or '-'}", flush=True)
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
