from __future__ import annotations

"""Gaze as a ray in the room, so a look at an object survives head turns and
shifts in the bed.

MediaPipe gives, for every frame, where the head is (in centimetres, camera
axes: x to the picture's right, y up, z toward the camera) and which way it
faces. The eye scores and the iris position say where the eyes point within
the head. Put together: a ray from the head along the gaze. An object is a
point in the room; to learn it the patient looks at it once and the ray is
followed out to the table distance. From then on a look counts when the ray
points at that point, from wherever the head is now.

This is why one look per object is enough: the head turn and the distance
are measured, not learned, so the calibration does not have to see every
posture the patient may take later.
"""

import json
import math

import numpy as np

from .features import HEAD_R, HEAD_T

IN_L, OUT_L, UP_L, DOWN_L, IN_R, OUT_R, UP_R, DOWN_R = range(8)
IRIS_A_ALONG, IRIS_A_ACROSS, IRIS_B_ALONG, IRIS_B_ACROSS = 8, 9, 10, 11


def eye_angles(feature, eye_gain: float, eye_clamp: float, iris_gain: float) -> tuple:
    """Where the eyes point within the head: (yaw, pitch) in radians,
    + is the patient's own left and up. Two estimates are averaged: the eye
    scores, which are smooth but saturate past the screen edge, and the iris
    position between the eye corners, which keeps going."""
    f = feature
    score_h = (f[OUT_L] + f[IN_R]) / 2 - (f[IN_L] + f[OUT_R]) / 2
    score_v = (f[UP_L] + f[UP_R]) / 2 - (f[DOWN_L] + f[DOWN_R]) / 2
    iris_h = (f[IRIS_A_ALONG] + f[IRIS_B_ALONG]) / 2 - 0.5
    iris_v = (f[IRIS_A_ACROSS] + f[IRIS_B_ACROSS]) / 2
    clamp = lambda v: max(-eye_clamp, min(eye_clamp, v))
    yaw = (clamp(eye_gain * score_h) + iris_gain * iris_h) / 2
    pitch = (clamp(eye_gain * score_v) + iris_gain * iris_v) / 2
    return float(yaw), float(pitch)


def unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def angle_between(a, b) -> float:
    return float(math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(unit(a), unit(b))))))))


class GazeRay:
    def __init__(self, eye_gain: float = 0.75, eye_clamp: float = 0.3, iris_gain: float = 2.5,
                 distance_cm: float = 70.0, accept_deg: float = 9.0, margin_deg: float = 2.0):
        self.eye_gain = eye_gain
        self.eye_clamp = eye_clamp
        self.iris_gain = iris_gain
        self.distance_cm = distance_cm
        self.accept_deg = accept_deg
        self.margin_deg = margin_deg
        self.points: dict = {}       # target id -> [x, y, z] in the room
        self.spread: dict = {}       # target id -> degrees the look wandered while learning
        self._last: dict = {}

    # --- geometry -------------------------------------------------------
    def ray(self, feature) -> tuple:
        """(origin, direction) of the gaze in camera axes, or None."""
        if feature is None or len(feature) < HEAD_R + 9:
            return None
        f = np.asarray(feature, dtype=float)
        if not np.isfinite(f).all():
            return None
        origin = f[HEAD_T:HEAD_T + 3]
        rot = f[HEAD_R:HEAD_R + 9].reshape(3, 3)
        yaw, pitch = eye_angles(f, self.eye_gain, self.eye_clamp, self.iris_gain)
        # In face axes the face looks along +z (toward the camera when facing
        # it) and +x is the patient's own left, so a look to the left tilts
        # the ray toward +x and a look up toward +y.
        local = np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch),
                          math.cos(yaw) * math.cos(pitch)])
        return origin, unit(rot @ local)

    def learn(self, target: str, features, distance_cm: float | None = None) -> None:
        """One look: follow the mean ray out to the table and keep the point."""
        rays = [r for r in (self.ray(f) for f in features) if r is not None]
        if len(rays) < 3:
            raise ValueError("too few readable frames to learn from")
        origin = np.mean([r[0] for r in rays], axis=0)
        direction = unit(np.mean([r[1] for r in rays], axis=0))
        if distance_cm is None:
            distance_cm = self.distance_cm
        self.points[target] = (origin + distance_cm * direction).tolist()
        self.spread[target] = round(float(np.mean([angle_between(r[1], direction) for r in rays])), 1)

    def forget(self, target: str | None = None) -> None:
        if target is None:
            self.points.clear()
            self.spread.clear()
        else:
            self.points.pop(target, None)
            self.spread.pop(target, None)

    def errors(self, feature) -> dict:
        """Degrees between the gaze and the line to each learned point."""
        r = self.ray(feature)
        if r is None:
            return {}
        origin, direction = r
        return {t: round(angle_between(direction, np.asarray(p) - origin), 1)
                for t, p in self.points.items()}

    def predict(self, feature) -> str | None:
        """The target the patient is looking at, or None for anywhere else."""
        errs = self.errors(feature)
        self._last = errs
        if not errs:
            return None
        ranked = sorted(errs.items(), key=lambda kv: kv[1])
        best, err = ranked[0]
        if err > self.accept_deg:
            return None
        if len(ranked) > 1 and ranked[1][1] - err < self.margin_deg:
            return None
        return best

    def offset_from(self, target: str, feature) -> tuple | None:
        """How far the gaze is from the line to a target, as (left, up) in
        degrees from the patient's point of view. For YES/NO beside the screen."""
        r = self.ray(feature)
        if r is None or target not in self.points:
            return None
        origin, direction = r
        to = unit(np.asarray(self.points[target]) - origin)
        yaw = math.degrees(math.atan2(direction[0], direction[2]) - math.atan2(to[0], to[2]))
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, direction[1]))) - math.asin(max(-1.0, min(1.0, to[1]))))
        return yaw, pitch

    def last(self) -> dict:
        return dict(self._last)

    def gaze_angles(self, feature, anchor=None) -> tuple | None:
        """(left, up) in degrees: the gaze relative to the line to `anchor`
        (a learned point), or to the camera when there is none. + is the
        patient's left and up."""
        r = self.ray(feature)
        if r is None:
            return None
        origin, direction = r
        ref = unit((np.asarray(anchor) if anchor is not None else np.zeros(3)) - origin)
        yaw = math.degrees(math.atan2(direction[0], direction[2]) - math.atan2(ref[0], ref[2]))
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, direction[1]))) - math.asin(max(-1.0, min(1.0, ref[1]))))
        return round(yaw, 1), round(pitch, 1)

    # Before any spot is taught the dot still moves: the photo is assumed to
    # be taken from the patient's seat, about this many degrees across.
    DEFAULT_FOV = (50.0, 40.0)

    def project(self, feature, uv: dict, screen: str | None = "rest"):
        """Where the gaze falls on the table photo, as fractions (u, v).
        With three or more taught spots the map is affine; with two, a scale
        per axis; with one, that spot anchors a default scale; with none, the
        camera direction anchors the middle of the photo."""
        r = self.ray(feature)
        if r is None:
            return None
        origin, direction = r

        def ang(v):
            v = unit(v)
            return (math.atan2(v[0], v[2]), math.asin(max(-1.0, min(1.0, v[1]))))

        known = [(t, self.points[t], uv[t]) for t in uv if t in self.points]
        g = np.array(ang(direction))
        fov = np.radians(self.DEFAULT_FOV)
        if len(known) >= 3:
            A = np.array([ang(np.asarray(p) - origin) for _, p, _ in known])
            U = np.array([u for _, _, u in known], dtype=float)
            X = np.hstack([A, np.ones((len(A), 1))])
            coef, *_ = np.linalg.lstsq(X, U, rcond=None)
            out = np.array([g[0], g[1], 1.0]) @ coef
        elif len(known) == 2:
            A = np.array([ang(np.asarray(p) - origin) for _, p, _ in known])
            U = np.array([u for _, _, u in known], dtype=float)
            dA, dU = A[1] - A[0], U[1] - U[0]
            s_u = dU[0] / dA[0] if abs(dA[0]) > 0.05 else -1 / fov[0]
            s_v = dU[1] / dA[1] if abs(dA[1]) > 0.05 else -1 / fov[1]
            out = U[0] + np.array([s_u * (g[0] - A[0][0]), s_v * (g[1] - A[0][1])])
        else:
            if known:
                _, p, u0 = known[0]
                a0 = np.array(ang(np.asarray(p) - origin))
            else:
                # The camera sits by the screen, and the screen is where the
                # photo's own screen box is, else the middle of the photo.
                a0 = np.array(ang(-origin))
                u0 = uv.get(screen, (0.5, 0.5)) if screen else (0.5, 0.5)
            out = np.asarray(u0, dtype=float) + np.array([-(g[0] - a0[0]) / fov[0], -(g[1] - a0[1]) / fov[1]])
        return [round(float(min(1.1, max(-0.1, out[0]))), 3), round(float(min(1.1, max(-0.1, out[1]))), 3)]

    def self_check(self, samples: dict) -> dict:
        """Share of each target's own frames read as that target."""
        out = {}
        for target, features in samples.items():
            hits = sum(self.predict(f) == target for f in features)
            out[target] = round(hits / len(features), 3) if features else 0.0
        return out

    # --- persistence ----------------------------------------------------
    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"points": self.points, "spread": self.spread}, f)

    def load(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.points = {k: [float(x) for x in v] for k, v in data["points"].items()}
        self.spread = dict(data.get("spread", {}))
