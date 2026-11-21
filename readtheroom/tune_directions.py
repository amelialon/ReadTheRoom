"""Find better quick-mode direction settings from saved recordings.

    python -m readtheroom.tune_directions data/directions-*.json

Each recording comes from `python -m readtheroom.check_directions`. The
tuner tries weighted combinations of the eye-direction scores, the iris
position and the head angle on each axis, with a grid of thresholds, and
scores every frame with exactly the rule DirectionReader.read uses:
sideways first, then up, otherwise centre ("down" steps count as centre).
The first centre look of each recording is the centre reference and is not
scored, as in the app.

The signs of the iris and head terms are read from the data (left steps
must come out positive), so a term whose sign the recording cannot show is
left at 0. The simplest weighting within 2 points of the best accuracy
wins, to keep from fitting noise. With several recordings it also reports
each recording's accuracy with settings chosen on the others.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from dataclasses import dataclass, fields

import numpy as np

from .config import Config
from .directions import CENTRE, DOWN, LEFT, RIGHT, SIGNAL_NAMES, UP, signals

EYE_H, EYE_V, IRIS_H, IRIS_V, HEAD_YAW, HEAD_PITCH = range(6)
LABELS = (CENTRE, LEFT, RIGHT, UP)
CODE = {label: i for i, label in enumerate(LABELS)}

# Weights are sign * magnitude; magnitudes are searched, signs come from the data.
IRIS_MAGNITUDES = (0.0, 0.5, 1.0, 2.0, 3.0, 4.0)
HEAD_MAGNITUDES = (0.0, 0.25, 0.5, 1.0, 1.5, 2.0)
H_THRESHOLDS = tuple(round(0.08 + 0.02 * i, 2) for i in range(12))   # 0.08 .. 0.30
V_THRESHOLDS = tuple(round(0.06 + 0.02 * i, 2) for i in range(10))   # 0.06 .. 0.24
WITHIN_POINTS = 2.0  # "as good" means within this many percentage points of the best


@dataclass
class Settings:
    h_threshold: float
    v_threshold: float
    iris_weight: float = 0.0
    head_weight: float = 0.0
    iris_v_weight: float = 0.0
    head_v_weight: float = 0.0

    @classmethod
    def current(cls) -> "Settings":
        cfg = Config()
        return cls(cfg.dir_h_threshold, cfg.dir_v_threshold, cfg.dir_iris_weight, cfg.dir_head_weight,
                   cfg.dir_iris_v_weight, cfg.dir_head_v_weight)

    def reader_kwargs(self) -> dict:
        return {"h_threshold": self.h_threshold, "v_threshold": self.v_threshold,
                "iris_weight": self.iris_weight, "head_weight": self.head_weight,
                "iris_v_weight": self.iris_v_weight, "head_v_weight": self.head_v_weight}

    def complexity(self) -> tuple:
        weights = (self.iris_weight, self.head_weight, self.iris_v_weight, self.head_v_weight)
        return (sum(1 for w in weights if w), sum(abs(w) for w in weights))

    def config_lines(self) -> list[str]:
        names = {"h_threshold": "dir_h_threshold", "v_threshold": "dir_v_threshold",
                 "iris_weight": "dir_iris_weight", "head_weight": "dir_head_weight",
                 "iris_v_weight": "dir_iris_v_weight", "head_v_weight": "dir_head_v_weight"}
        return [f"    {names[f.name]}: float = {getattr(self, f.name):g}" for f in fields(self)]


@dataclass
class Step:
    name: str
    expected: str | None          # centre / left / right / up, or None = not scored
    table: np.ndarray             # frames x 6 signals, in SIGNAL_NAMES order
    raw_expected: str | None = None

    @property
    def is_head(self) -> bool:
        return self.name.startswith("head")

    @property
    def is_natural(self) -> bool:
        return self.name.startswith("natural")

    @property
    def is_down(self) -> bool:
        return self.raw_expected == DOWN or self.name == DOWN


@dataclass
class Recording:
    path: str
    steps: list
    has_iris: bool

    def centre_step(self) -> Step:
        for step in self.steps:
            if step.expected == CENTRE and len(step.table):
                return step
        raise ValueError(f"{self.path}: no readable centre step to learn the centre from")

    def scored_steps(self) -> list:
        ref = self.centre_step()
        return [s for s in self.steps if s is not ref and s.expected is not None and len(s.table)]

    def centre(self) -> np.ndarray:
        return self.centre_step().table.mean(axis=0)


def expected_label(name: str, expected) -> str | None:
    """What a step should read as. Down counts as centre (the reader never
    says down). Steps saved with no expectation, such as "head left", are
    read from their name so head turns are scored like any other look."""
    if expected == DOWN:
        return CENTRE
    if expected in LABELS:
        return expected
    for direction in (LEFT, RIGHT, UP):
        if direction in name.split():
            return direction
    return None


def recording_from_steps(steps, path: str = "synthetic") -> Recording:
    """A recording from (name, expected, [feature vector, ...]) triples."""
    out = []
    for name, expected, features in steps:
        rows = [[signals(f)[k] for k in SIGNAL_NAMES] for f in features]
        table = np.asarray(rows, dtype=float).reshape(-1, len(SIGNAL_NAMES))
        out.append(Step(name, expected_label(name, expected), table, expected))
    return Recording(path, out, has_iris=True)


def recording_from_dict(data: dict, path: str = "") -> Recording:
    """A saved recording. Newer files carry the raw feature vector per frame;
    older ones only eye and head signals, so their iris columns are zero and
    `has_iris` is False."""
    steps, has_iris = [], True
    for s in data["steps"]:
        if s.get("features"):
            rows = [[signals(f)[k] for k in SIGNAL_NAMES] for f in s["features"]]
        else:
            sig = s.get("signals", {})
            n = len(sig.get("eye_h", []))
            if n and not sig.get("iris_h"):
                has_iris = False
            rows = [[float(sig.get(k, [0.0] * n)[i]) for k in SIGNAL_NAMES] for i in range(n)]
        table = np.asarray(rows, dtype=float).reshape(-1, len(SIGNAL_NAMES))
        steps.append(Step(s["name"], expected_label(s["name"], s.get("expected")), table, s.get("expected")))
    return Recording(path, steps, has_iris)


def load_recording(path: str) -> Recording:
    with open(path, encoding="utf-8") as f:
        return recording_from_dict(json.load(f), path)


# --- signs -------------------------------------------------------------------

def _mean(recs, pick, column) -> tuple:
    """Mean and spread of one signal over the frames of the chosen steps,
    measured from each recording's centre."""
    parts = []
    for rec in recs:
        c = rec.centre()[column]
        parts += [s.table[:, column] - c for s in rec.steps if pick(s) and len(s.table)]
    if not parts:
        return None, None
    values = np.concatenate(parts)
    return float(values.mean()), float(values.std())


def _sign_from(recs, positive, negative, column) -> tuple:
    """+1 / -1 when the chosen steps separate on this signal, None when the
    recording gives no (or too little) evidence. Also a short reason."""
    pos, pos_sd = _mean(recs, positive, column)
    if pos is None:
        return None, "no step shows it"
    if negative is not None:
        neg, neg_sd = _mean(recs, negative, column)
    else:
        neg, neg_sd = None, None
    if neg is None:
        neg, neg_sd = 0.0, 0.0  # measured against the centre look itself
    diff = pos - neg
    spread = max(pos_sd or 0.0, neg_sd or 0.0, 1e-9)
    if abs(diff) < spread:
        return None, f"too weak to trust (difference {diff:+.3f}, spread {spread:.3f})"
    return (1 if diff > 0 else -1), f"difference {diff:+.3f}, spread {spread:.3f}"


def detect_signs(recs) -> dict:
    """Which way each extra signal points, read from the steps:
    iris_h: eye-only and natural left looks against right looks.
    head_yaw: the "head left" step against "head right" (natural looks if
    there are no head steps). iris_v, head_pitch: up against down.
    Each value is (sign or None, reason). None means the weight stays 0."""
    out = {}
    if not all(r.has_iris for r in recs):
        out["iris_h"] = (None, "no raw features saved; record again with the current check_directions")
        out["iris_v"] = out["iris_h"]
    else:
        out["iris_h"] = _sign_from(recs, lambda s: s.expected == LEFT and not s.is_head,
                                   lambda s: s.expected == RIGHT and not s.is_head, IRIS_H)
        out["iris_v"] = _sign_from(recs, lambda s: s.expected == UP, lambda s: s.is_down, IRIS_V)
    if any(s.is_head for r in recs for s in r.steps):
        out["head_yaw"] = _sign_from(recs, lambda s: s.is_head and s.expected == LEFT,
                                     lambda s: s.is_head and s.expected == RIGHT, HEAD_YAW)
    elif any(s.is_natural for r in recs for s in r.steps):
        sign, why = _sign_from(recs, lambda s: s.is_natural and s.expected == LEFT,
                               lambda s: s.is_natural and s.expected == RIGHT, HEAD_YAW)
        out["head_yaw"] = (sign, why + " (from the natural looks; there are no head steps)")
    else:
        out["head_yaw"] = (None, "no head or natural steps in the recording")
    out["head_pitch"] = _sign_from(recs, lambda s: s.expected == UP, lambda s: s.is_down, HEAD_PITCH)
    return out


# --- scoring -----------------------------------------------------------------

def _frames(recs):
    """All scored frames: deltas from each recording's centre (n x 6), the
    expected label code per frame, and (recording index, step) per frame."""
    deltas, codes, owners = [], [], []
    for r_i, rec in enumerate(recs):
        c = rec.centre()
        for step in rec.scored_steps():
            deltas.append(step.table - c)
            codes.append(np.full(len(step.table), CODE[step.expected], dtype=np.int8))
            owners += [(r_i, step)] * len(step.table)
    if not deltas:
        raise ValueError("nothing to score: every recording needs steps after its first centre look")
    return np.concatenate(deltas), np.concatenate(codes), owners


def read_frames(deltas: np.ndarray, st: Settings) -> np.ndarray:
    """Label code per frame with the DirectionReader rule, from deltas to centre."""
    h = deltas[:, EYE_H] + st.iris_weight * deltas[:, IRIS_H] + st.head_weight * deltas[:, HEAD_YAW]
    v = deltas[:, EYE_V] + st.iris_v_weight * deltas[:, IRIS_V] + st.head_v_weight * deltas[:, HEAD_PITCH]
    side = np.where(np.abs(h) >= st.h_threshold, np.where(h > 0, CODE[LEFT], CODE[RIGHT]), CODE[CENTRE])
    up = np.where(v >= st.v_threshold, CODE[UP], CODE[CENTRE])
    return np.where(side != CODE[CENTRE], side, up).astype(np.int8)


def score(recs, st: Settings) -> dict:
    """Per-step and overall accuracy. Overall is the share of all scored
    frames read as asked, like check_directions reports it."""
    deltas, codes, owners = _frames(recs)
    got = read_frames(deltas, st)
    rows, seen = [], {}
    for ok, (r_i, step) in zip(got == codes, owners):
        key = (r_i, step.name)
        if key not in seen:
            seen[key] = {"recording": recs[r_i].path, "step": step.name, "expected": step.expected,
                         "frames": 0, "correct": 0}
            rows.append(seen[key])
        seen[key]["frames"] += 1
        seen[key]["correct"] += int(ok)
    return {"steps": rows, "frames": int(len(codes)), "correct": int((got == codes).sum()),
            "accuracy": float((got == codes).mean())}


def search(recs, signs: dict | None = None) -> Settings:
    """Grid search over weights and thresholds, then the simplest weighting
    within WITHIN_POINTS of the best accuracy."""
    signs = detect_signs(recs) if signs is None else signs
    deltas, codes, _ = _frames(recs)

    def magnitudes(key, mags):
        sign = signs[key][0]
        return [0.0] if sign is None else [sign * m if m else 0.0 for m in mags]

    h_cands = [(wi, wh, th) for wi in magnitudes("iris_h", IRIS_MAGNITUDES)
               for wh in magnitudes("head_yaw", HEAD_MAGNITUDES) for th in H_THRESHOLDS]
    v_cands = [(wi, wh, tv) for wi in magnitudes("iris_v", IRIS_MAGNITUDES)
               for wh in magnitudes("head_pitch", HEAD_MAGNITUDES) for tv in V_THRESHOLDS]

    h_all = np.stack([deltas[:, EYE_H] + wi * deltas[:, IRIS_H] + wh * deltas[:, HEAD_YAW]
                      for wi, wh, _ in h_cands])
    th = np.array([c[2] for c in h_cands])[:, None]
    sideways = np.abs(h_all) >= th
    side_ok = ((sideways & (h_all > 0) & (codes == CODE[LEFT]))
               | (sideways & (h_all <= 0) & (codes == CODE[RIGHT]))).sum(axis=1)   # (nH,)
    not_side = (~sideways).astype(np.float32)
    v_all = np.stack([deltas[:, EYE_V] + wi * deltas[:, IRIS_V] + wh * deltas[:, HEAD_PITCH]
                      for wi, wh, _ in v_cands])
    up = v_all >= np.array([c[2] for c in v_cands])[:, None]                  # (nV, n)
    up_ok = (up & (codes == CODE[UP])).astype(np.float32)
    centre_ok = (~up & (codes == CODE[CENTRE])).astype(np.float32)
    correct = side_ok[:, None] + not_side @ up_ok.T + not_side @ centre_ok.T   # (nH, nV)
    acc = correct / len(codes)

    best = acc.max()
    good = np.argwhere(acc >= best - WITHIN_POINTS / 100 - 1e-12)
    cur = Settings.current()

    def rank(ij):
        # Among the settings that are as good as the best: fewest extra terms
        # first, then the thresholds nearest to what the app runs now, then
        # the smallest weights, then accuracy. So a change has to buy more
        # than WITHIN_POINTS, and a weight never buys a jumpier threshold.
        i, j = ij
        st = Settings(h_cands[i][2], v_cands[j][2], h_cands[i][0], h_cands[i][1], v_cands[j][0], v_cands[j][1])
        n_terms, size = st.complexity()
        return (n_terms, abs(st.h_threshold - cur.h_threshold) + abs(st.v_threshold - cur.v_threshold),
                size, -acc[i, j])

    i, j = min(map(tuple, good), key=rank)
    return Settings(h_cands[i][2], v_cands[j][2], h_cands[i][0], h_cands[i][1], v_cands[j][0], v_cands[j][1])


# --- report ------------------------------------------------------------------

def explain(cur: Settings, best: Settings, acc_cur: float, acc_best: float) -> str:
    gain = 100 * (acc_best - acc_cur)
    parts = []
    terms = {"iris_weight": "iris position (sideways)", "head_weight": "head turn (sideways)",
             "iris_v_weight": "iris position (up)", "head_v_weight": "head tilt (up)"}
    for name, what in terms.items():
        w = getattr(best, name)
        if w != getattr(cur, name):
            parts.append(f"adds {what} with weight {w:+g}" if w else f"drops the {what} term")
    if (best.h_threshold, best.v_threshold) != (cur.h_threshold, cur.v_threshold):
        parts.append(f"thresholds {best.h_threshold:g} sideways / {best.v_threshold:g} up")
    if not parts:
        return "The current settings are already the simplest that reach the best accuracy; nothing to change."
    return f"Best found {', '.join(parts)}: {100 * acc_best:.1f}% of frames instead of {100 * acc_cur:.1f}% ({gain:+.1f} points)."


def print_table(recs, cur: Settings, best: Settings, out=None) -> None:
    out = out or sys.stdout
    a, b = score(recs, cur), score(recs, best)
    many = len(recs) > 1
    print(f"{'step':<18}{'expected':<9}{'frames':>7}{'current':>10}{'best':>8}" + ("   recording" if many else ""), file=out)
    for ra, rb in zip(a["steps"], b["steps"]):
        line = (f"{ra['step']:<18}{ra['expected']:<9}{ra['frames']:>7}"
                f"{100 * ra['correct'] / ra['frames']:>9.0f}%{100 * rb['correct'] / rb['frames']:>7.0f}%")
        print(line + (f"   {ra['recording']}" if many else ""), file=out)
    print(f"{'overall':<18}{'':<9}{a['frames']:>7}{100 * a['accuracy']:>9.0f}%{100 * b['accuracy']:>7.0f}%", file=out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="readtheroom.tune_directions", description=__doc__.split("\n\n")[1])
    p.add_argument("recordings", nargs="+", help="data/directions-*.json from check_directions")
    args = p.parse_args(argv)
    paths = sorted({path for pattern in args.recordings for path in (glob.glob(pattern) or [pattern])})
    recs = [load_recording(path) for path in paths]
    for rec in recs:
        rec.centre_step()  # fail early with a plain message

    print(f"Recordings: {len(recs)}, scored frames: {sum(len(s.table) for r in recs for s in r.scored_steps())}")
    for rec in recs:
        names = [s.name for s in rec.steps]
        notes = []
        if not rec.has_iris:
            notes.append("no raw features saved, so the iris position cannot be used")
        if not any(s.is_head for s in rec.steps):
            notes.append("no head-turn steps, so the head weight cannot be chosen from it")
        if not any(s.is_natural for s in rec.steps):
            notes.append("no natural looks")
        print(f"  {rec.path}: {len(rec.steps)} steps ({names[0]} .. {names[-1]})"
              + (f"; {'; '.join(notes)}" if notes else ""))

    signs = detect_signs(recs)
    print("\nSigns read from the data (+ means the term already points the patient's left / up):")
    for key, (sign, why) in signs.items():
        shown = {1: "+", -1: "-", None: "cannot tell, weight stays 0"}[sign]
        print(f"  {key:<11}{shown:<30}{why}")

    cur, best = Settings.current(), search(recs, signs)
    print(f"\nPer step: share of frames read as asked. Current = Config as it is now "
          f"({cur.h_threshold:g} / {cur.v_threshold:g}, weights"
          f" {cur.iris_weight:g} {cur.head_weight:g} {cur.iris_v_weight:g} {cur.head_v_weight:g}).")
    print_table(recs, cur, best)
    acc_cur, acc_best = score(recs, cur)["accuracy"], score(recs, best)["accuracy"]

    if len(recs) > 1:
        print("\nEach recording scored with settings chosen on the other recordings (a fair check):")
        print(f"{'recording':<40}{'frames':>7}{'current':>10}{'pooled best':>13}{'others only':>13}")
        for k, rec in enumerate(recs):
            others = recs[:k] + recs[k + 1:]
            held = search(others)
            s_cur, s_pool, s_held = (score([rec], st)["accuracy"] for st in (cur, best, held))
            print(f"{rec.path:<40}{score([rec], cur)['frames']:>7}{100 * s_cur:>9.0f}%{100 * s_pool:>12.0f}%{100 * s_held:>12.0f}%")

    print("\nRecommended Config values (readtheroom/config.py):")
    print("\n".join(best.config_lines()))
    print("\n" + explain(cur, best, acc_cur, acc_best))
    return 0


if __name__ == "__main__":
    sys.exit(main())
