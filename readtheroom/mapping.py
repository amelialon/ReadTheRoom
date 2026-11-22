"""Gaze -> a point in the scene camera's photo, fitted from a short calibration.

The patient looks at a few things whose place in the photo is known (the
detected objects, the screen); a straight-line (affine) map from eye readings
to photo pixels is fitted to those looks, with a perspective map (homography)
as the one bendier alternative worth comparing on so few points.
"""
from __future__ import annotations

import numpy as np


def _rows(x):
    x = np.atleast_2d(np.asarray(x, dtype=float))
    return np.column_stack([x, np.ones(len(x))])


def _normaliser(pts):
    """Shift to mean 0 and scale to mean distance sqrt(2): keeps the
    homography solve well conditioned when eye readings are ~0.1 and pixels ~1000."""
    mean = pts.mean(axis=0)
    scale = np.sqrt(2) / max(np.mean(np.linalg.norm(pts - mean, axis=1)), 1e-12)
    return np.array([[scale, 0, -scale * mean[0]], [0, scale, -scale * mean[1]], [0, 0, 1]])


def _homography(src, dst):
    """Least-squares perspective map src -> dst (direct linear transform)."""
    Ts, Td = _normaliser(src), _normaliser(dst)
    s = _rows(src) @ Ts.T
    d = _rows(dst) @ Td.T
    rows = []
    for (x, y, _), (u, v, _) in zip(s, d):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    H = np.linalg.svd(np.array(rows))[2][-1].reshape(3, 3)
    return np.linalg.inv(Td) @ H @ Ts


def fit(samples: dict, points: dict, kind: str = "affine", ridge: float = 1e-6) -> dict:
    """samples: name -> eye readings (n, k); points: name -> [x, y] in the photo.
    Each target is reduced to its median reading first, so blinks and stray
    frames do not drag the fit. kind: "affine" (straight-line, any k) or
    "homography" (perspective, k = 2, needs 4+ targets)."""
    names = [n for n in samples if n in points]
    need = 4 if kind == "homography" else 3
    if len(names) < need:
        raise ValueError(f"a {kind} map needs {need} targets, got {len(names)}")
    X = np.array([np.median(np.atleast_2d(samples[n]), axis=0) for n in names])
    Y = np.array([points[n] for n in names], dtype=float)
    if kind == "homography":
        return {"kind": kind, "m": _homography(X, Y)}
    A = _rows(X)
    reg = ridge * np.diag([1.0] * X.shape[1] + [0.0])  # never shrink the offset
    return {"kind": kind, "m": np.linalg.solve(A.T @ A + reg, A.T @ Y)}


def predict(model: dict, readings) -> np.ndarray:
    """Photo point for one reading (k,), or points for many (n, k)."""
    rows = _rows(readings)
    if model["kind"] == "homography":
        p = rows @ model["m"].T
        out = p[:, :2] / p[:, 2:]
    else:
        out = rows @ model["m"]
    return out[0] if np.ndim(readings) == 1 else out


def _gap(point, box) -> float:
    """Distance from a point to a box's edge; 0 inside it."""
    dx = max(box[0] - point[0], 0.0, point[0] - box[2])
    dy = max(box[1] - point[1], 0.0, point[1] - box[3])
    return float(np.hypot(dx, dy))


def reach(targets: dict) -> float:
    """Half the edge-to-edge gap between the two closest targets, so a point
    can never be claimed by the wrong neighbour."""
    boxes = list(targets.values())
    gaps = []
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            dx = max(a[0] - b[2], b[0] - a[2], 0.0)
            dy = max(a[1] - b[3], b[1] - a[3], 0.0)
            gaps.append(float(np.hypot(dx, dy)))
    return min(gaps) / 2 if gaps else float("inf")


def nearest(point, targets: dict, radius: float = float("inf"), size=None):
    """Name of the target whose box is closest to the point, if within radius
    and (given the photo size) the point is on the photo; else None."""
    if size is not None and not (0 <= point[0] < size[0] and 0 <= point[1] < size[1]):
        return None
    name, gap = min(((n, _gap(point, b)) for n, b in targets.items()), key=lambda t: t[1])
    return name if gap <= radius else None


def leave_one_out(samples: dict, targets: dict, kind: str = "affine") -> dict:
    """For each target: fit without it, then predict where its looks land.
    Returns name -> {'point', 'error_px', 'nearest'}; a good map puts every
    held-out target nearest to itself."""
    need = 5 if kind == "homography" else 4  # one left out, the rest must still fit
    if len(samples) < need:
        raise ValueError(f"testing a {kind} map needs {need} recorded targets, got {len(samples)}")
    centres = {n: [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] for n, b in targets.items()}
    report = {}
    for name in samples:
        rest = {n: s for n, s in samples.items() if n != name}
        point = predict(fit(rest, centres, kind), np.median(np.atleast_2d(samples[name]), axis=0))
        report[name] = {"point": [round(float(v), 1) for v in point],
                        "error_px": round(float(np.hypot(*(point - centres[name]))), 1),
                        "nearest": nearest(point, targets)}
    return report
