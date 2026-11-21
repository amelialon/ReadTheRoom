"""Experiment: can this camera tell left / right / up / down / centre apart
after a single look at the centre of the screen?

    python -m readtheroom.check_directions --camera 0

Sit as the patient would, 50-70 cm from the screen with the camera on top.
A voice tells you where to look; keep your head roughly still and move your
eyes. It takes about a minute, prints a results table, and saves the numbers
to data/directions-*.json.

The last few steps ask you to look the way you naturally would, head and
eyes together. The saved file keeps every raw feature vector, so
`python -m readtheroom.tune_directions data/directions-*.json` can work out
how much the head and the iris position should count.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

from .config import Config
from .directions import CENTRE, LEFT, RIGHT, UP, DirectionReader, signals
from .features import FaceFeatureExtractor

# Eyes only, head still. "head left" and "head right" have no expected
# direction here because the eyes-only reader is not meant to catch them.
STEPS = [
    ("centre", CENTRE, "Look at the centre of the screen"),
    ("far left", LEFT, "Look far to your left, past the edge of the screen"),
    ("centre again", CENTRE, "Look at the centre of the screen"),
    ("far right", RIGHT, "Look far to your right, past the edge of the screen"),
    ("up", UP, "Look up, above the screen"),
    ("down", CENTRE, "Look down, below the screen"),  # down is ignored on purpose
    ("left edge", LEFT, "Look at the left edge of the screen"),
    ("right edge", RIGHT, "Look at the right edge of the screen"),
    ("centre last", CENTRE, "Look at the centre of the screen"),
    ("head left", None, "Turn your whole head to the left, as if looking at something there"),
    ("head right", None, "Turn your whole head to the right, as if looking at something there"),
]
# Head and eyes together, however feels natural. These are what the device
# meets in real use, so the tuner scores them like any other step.
NATURAL_STEPS = [
    ("natural centre", CENTRE, "From now on, move your head too if that feels natural. Look at the screen"),
    ("natural left", LEFT, "Look at something on your left, the way you naturally would"),
    ("natural centre 2", CENTRE, "Look at the screen"),
    ("natural right", RIGHT, "Look at something on your right, the way you naturally would"),
    ("natural centre 3", CENTRE, "Look at the screen"),
]
ALL_STEPS = STEPS + NATURAL_STEPS


def announce(text: str) -> None:
    print(f"\n>>> {text}", flush=True)
    if shutil.which("say"):
        subprocess.run(["say", text])
    else:
        print("\a", end="", flush=True)


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = argparse.ArgumentParser(prog="readtheroom.check_directions")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--model", default=os.path.join(root, "models", "face_landmarker.task"))
    p.add_argument("--settle", type=float, default=1.0)
    p.add_argument("--record", type=float, default=2.0)
    args = p.parse_args()

    from .camera import OpenCVCamera

    extractor = FaceFeatureExtractor(args.model)
    camera = OpenCVCamera(args.camera)
    for _ in range(15):  # let the camera settle its exposure
        camera.read()

    announce("Keep your head still and move only your eyes. Starting now.")
    recorded = []  # (name, expected, [feature, ...], frames seen)
    for name, expected, instruction in ALL_STEPS:
        announce(instruction)
        time.sleep(args.settle)
        features, seen = [], 0
        end = time.monotonic() + args.record
        while time.monotonic() < end:
            frame = camera.read()
            if frame is None:
                continue
            seen += 1
            _, feature = extractor(frame, int(time.monotonic() * 1000))
            if feature is not None:
                features.append(feature)
        recorded.append((name, expected, features, seen))
    announce("Done. Thank you.")

    if not recorded[0][2]:
        raise SystemExit("The eyes were not readable while looking at the centre. "
                         "Check lighting and distance with check_camera.")
    cfg = Config()  # read with exactly what the app would use
    reader = DirectionReader(cfg.dir_h_threshold, cfg.dir_v_threshold, cfg.dir_adapt,
                             iris_weight=cfg.dir_iris_weight, head_weight=cfg.dir_head_weight,
                             iris_v_weight=cfg.dir_iris_v_weight, head_v_weight=cfg.dir_head_v_weight)
    reader.set_centre(recorded[0][2])  # the one-point calibration

    print("\nSignals (average, with spread in brackets). eye_h: + is the patient's left. eye_v: + is up.")
    shown = ("eye_h", "eye_v", "iris_h", "head_yaw", "head_pitch")
    print(f"{'step':<17}{'frames':>8}" + "".join(f"{k:>16}" for k in shown) + "   read as")
    out = {"version": 2, "centre": reader.centre,
           "thresholds": [reader.h_threshold, reader.v_threshold], "weights": reader.weights,
           "steps": []}
    correct_total = frames_total = 0
    for name, expected, features, seen in recorded:
        sig = [signals(f) for f in features]
        cols = {}
        for key in shown:
            values = [s[key] for s in sig]
            cols[key] = (statistics.fmean(values), statistics.pstdev(values)) if values else (float("nan"), 0.0)
        reads = [reader.read(f) for f in features]
        counts = {d: reads.count(d) for d in (CENTRE, LEFT, RIGHT, UP) if reads.count(d)}
        correct = reads.count(expected)
        if name != "centre" and expected:  # the first centre is what it learned from
            correct_total += correct
            frames_total += len(reads)
        share = (f"{100 * correct / len(reads):.0f}% {expected}" if reads and expected
                 else "head turn: see head_yaw" if reads else "no readable frames")
        print(f"{name:<17}{len(features):>4}/{seen:<3}"
              + "".join(f"{m:>9.3f} ({s:.3f})" for m, s in cols.values())
              + f"   {share}  {counts}")
        keys = sig[0].keys() if sig else shown
        out["steps"].append({"name": name, "expected": expected, "frames": len(features), "seen": seen,
                             "signals": {k: [round(s[k], 4) for s in sig] for k in keys},
                             "features": [[round(float(x), 5) for x in f] for f in features],
                             "reads": counts})
    if frames_total:
        print(f"\nOverall, with the app's current settings: {100 * correct_total / frames_total:.0f}% of frames "
              "read as the direction asked for (the first centre look is excluded).")
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    path = os.path.join(root, "data", f"directions-{time.strftime('%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    print(f"Saved to {path}")
    print(f"To find better settings: python -m readtheroom.tune_directions {path}")


if __name__ == "__main__":
    main()
