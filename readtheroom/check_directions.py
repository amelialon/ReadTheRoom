"""Experiment: can this camera tell left / right / up / down / centre apart
after a single look at the centre of the screen?

    python -m readtheroom.check_directions --camera 0

Sit as the patient would, 50-70 cm from the screen with the camera on top.
A voice tells you where to look; keep your head roughly still and move your
eyes. It takes about a minute, prints a results table, and saves the numbers
to data/directions-*.json.
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

from .directions import CENTRE, LEFT, RIGHT, UP, DirectionReader, signals
from .features import FaceFeatureExtractor

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
    for name, expected, instruction in STEPS:
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
    reader = DirectionReader()
    reader.set_centre(recorded[0][2])  # the one-point calibration

    print("\nSignals (average, with spread in brackets). eye_h: + is the patient's left. eye_v: + is up.")
    print(f"{'step':<13}{'frames':>8}{'eye_h':>16}{'eye_v':>16}{'head_yaw':>16}{'head_pitch':>16}   read as")
    out = {"centre": reader.centre, "thresholds": [reader.h_threshold, reader.v_threshold], "steps": []}
    correct_total = frames_total = 0
    for name, expected, features, seen in recorded:
        sig = [signals(f) for f in features]
        cols = {}
        for key in ("eye_h", "eye_v", "head_yaw", "head_pitch"):
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
        print(f"{name:<13}{len(features):>4}/{seen:<3}"
              + "".join(f"{m:>9.3f} ({s:.3f})" for m, s in cols.values())
              + f"   {share}  {counts}")
        out["steps"].append({"name": name, "expected": expected, "frames": len(features), "seen": seen,
                             "signals": {k: [round(s[k], 4) for s in sig] for k in cols},
                             "reads": counts})
    if frames_total:
        print(f"\nOverall, with first-guess thresholds: {100 * correct_total / frames_total:.0f}% of frames "
              "read as the direction asked for (the first centre look is excluded).")
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    path = os.path.join(root, "data", f"directions-{time.strftime('%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    print(f"Saved to {path}")


if __name__ == "__main__":
    main()
