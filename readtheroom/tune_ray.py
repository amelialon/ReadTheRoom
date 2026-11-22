"""Check the gaze ray against real taught looks, and pick the eye gains.

    python -m readtheroom.tune_ray data/teach-*.json

Every Teach on the caregiver page saves its frames to data/teach-HHMMSS.json.
This reads them, re-learns each spot from its first look, and reports how
each look is read: the share of its frames that land on their own spot, and
how far the look wandered. Then it tries a range of eye gains (how much the
eyes count against the head) and prints the one that tells the spots apart
best, as Config values.

Teach each spot twice, the second time from a different posture (head turned,
leaning), and the second looks become a fair test of the first.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

import numpy as np

from .config import Config
from .gazeray import GazeRay


def load(paths: list) -> dict:
    """target -> [look, look, ...], each look a list of frames, in time order."""
    looks = defaultdict(list)
    records = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
        if r.get("frames"):
            r["path"] = path
            records.append(r)
    for r in sorted(records, key=lambda r: r["time"]):
        looks[r["target"]].append({"frames": [np.asarray(f) for f in r["frames"]],
                                   "label": r.get("label", r["target"]), "path": r["path"]})
    return looks


def evaluate(looks: dict, cfg: Config, scale: float) -> tuple:
    """Learn every spot from its first look; score every look. Returns
    (overall share read correctly, per-look rows, spread per target)."""
    ray = GazeRay(cfg.ray_eye_gain * scale, cfg.ray_eye_clamp * scale, cfg.ray_iris_gain * scale,
                  cfg.ray_distance_cm, cfg.ray_accept_deg, cfg.ray_margin_deg)
    for target, series in looks.items():
        frames = series[0]["frames"]
        distance = None
        if target in ("rest", "yes", "no"):
            origins = [r[0] for r in (ray.ray(f) for f in frames) if r is not None]
            if origins:
                distance = float(np.mean([np.linalg.norm(o) for o in origins]))
        try:
            ray.learn(target, frames, distance)
        except ValueError:
            continue
    rows, hits, total = [], 0, 0
    for target, series in looks.items():
        for i, look in enumerate(series):
            reads = [ray.predict(f) for f in look["frames"]]
            own = sum(r == target for r in reads)
            nothing = sum(r is None for r in reads)
            rows.append((look["label"], i + 1, len(reads), own / len(reads), nothing / len(reads),
                         ray.spread.get(target)))
            if i > 0:  # second and later looks are the fair test
                hits += own
                total += len(reads)
    overall = hits / total if total else float("nan")
    return overall, rows, ray


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="readtheroom.tune_ray")
    p.add_argument("files", nargs="+", help="data/teach-*.json")
    args = p.parse_args(argv)
    cfg = Config()
    looks = load(args.files)
    if not looks:
        sys.exit("no taught looks in those files")
    print(f"Spots: {', '.join(looks)}; looks: {sum(len(v) for v in looks.values())}")
    for t in ("rest", "yes", "no"):
        if t not in looks:
            print(f"'{t}' was not calibrated; the device will not be ready without it.")
    repeats = sum(len(v) > 1 for v in looks.values())
    if not repeats:
        print("Every spot was taught once. Teach each a second time from another posture "
              "to get a fair test; for now the scores below are each look read against itself.")

    print("\nWith the current settings (eye gain %.2f, iris gain %.2f):" % (cfg.ray_eye_gain, cfg.ray_iris_gain))
    _, rows, ray = evaluate(looks, cfg, 1.0)
    print(f"{'spot':18s} {'look':>4s} {'frames':>6s} {'own':>5s} {'none':>5s} {'wander':>7s}")
    for label, i, n, own, nothing, spread in rows:
        print(f"{label:18s} {i:4d} {n:6d} {own:5.0%} {nothing:5.0%} {spread if spread is None else f'{spread:5.1f}deg':>7}")

    print("\nEye gains tried (both the score and the iris gain scaled together):")
    best = None
    for scale in (0.5, 0.65, 0.8, 1.0, 1.25, 1.5, 2.0):
        overall, rows, _ = evaluate(looks, cfg, scale)
        own_all = np.mean([r[3] for r in rows])
        score = overall if overall == overall else own_all  # fair test if we have one
        print(f"  x{scale:<5} fair test {overall:5.0%}   all looks {own_all:5.0%}" if overall == overall
              else f"  x{scale:<5} all looks {own_all:5.0%}")
        if best is None or score > best[1] + 1e-9:
            best = (scale, score)
    scale = best[0]
    print("\nRecommended Config values (readtheroom/config.py):")
    print(f"    ray_eye_gain: float = {cfg.ray_eye_gain * scale:.2f}")
    print(f"    ray_eye_clamp: float = {cfg.ray_eye_clamp * scale:.2f}")
    print(f"    ray_iris_gain: float = {cfg.ray_iris_gain * scale:.2f}")
    if scale == 1.0:
        print("The current gains are already the best of those tried.")
    print("\nA spot whose 'own' share is low on its second look is the one to move further from the others.")


if __name__ == "__main__":
    main()
