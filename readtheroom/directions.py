from __future__ import annotations

"""Coarse gaze direction (left / right / up / down / centre) with no trained
calibration: only one look at the centre is needed, to learn where "centre"
is for this person and camera position. After GaVe (Zeng et al., 2023).

The thresholds come from one person on one laptop camera (see
`python -m readtheroom.check_directions`), so re-measure them on the demo
hardware. Two things that measurement showed:
- Looking down cannot be told from half-closed eyes, so "down" is not used.
- Looking sideways also lifts the up/down signal, so sideways is decided first.

By default only MediaPipe's eye-direction scores are used. The reader can
also add the iris position and the head angle to each axis, with weights
found by `python -m readtheroom.tune_directions` from saved recordings.
"""

# Positions in the feature vector from features.py
IN_L, OUT_L, UP_L, DOWN_L, IN_R, OUT_R, UP_R, DOWN_R = range(8)
IRIS_A_ALONG, IRIS_A_ACROSS, IRIS_B_ALONG, IRIS_B_ACROSS = 8, 9, 10, 11
HEAD_YAW, HEAD_PITCH = 12, 13

CENTRE, LEFT, RIGHT, UP, DOWN = "centre", "left", "right", "up", "down"

# The raw signals a direction is read from, in the order the weights apply.
SIGNAL_NAMES = ("eye_h", "eye_v", "iris_h", "iris_v", "head_yaw", "head_pitch")


def signals(feature) -> dict:
    """Named direction signals from one feature vector.

    eye_h > 0 means the eyes are turned toward the patient's own left;
    eye_v > 0 means up. The iris offsets (iris position between the eye
    corners, both eyes averaged) and the head angles are passed through
    unchanged, because their sign depends on the camera and is measured
    from recordings by the tuner.
    """
    f = feature
    return {
        "eye_h": float((f[OUT_L] + f[IN_R]) / 2 - (f[IN_L] + f[OUT_R]) / 2),
        "eye_v": float((f[UP_L] + f[UP_R]) / 2 - (f[DOWN_L] + f[DOWN_R]) / 2),
        "iris_h": float((f[IRIS_A_ALONG] + f[IRIS_B_ALONG]) / 2),
        "iris_v": float((f[IRIS_A_ACROSS] + f[IRIS_B_ACROSS]) / 2),
        "head_yaw": float(f[HEAD_YAW]),
        "head_pitch": float(f[HEAD_PITCH]),
    }


def combine(s: dict, iris_weight: float = 0.0, head_weight: float = 0.0,
            iris_v_weight: float = 0.0, head_v_weight: float = 0.0) -> tuple:
    """The two numbers a direction is decided on: (sideways, up). Each is the
    eye score plus weighted iris and head terms; the weights carry the sign."""
    h = s["eye_h"] + iris_weight * s["iris_h"] + head_weight * s["head_yaw"]
    v = s["eye_v"] + iris_v_weight * s["iris_v"] + head_v_weight * s["head_pitch"]
    return h, v


class DirectionReader:
    """Turns signals into left / right / up / centre, relative to a learned centre."""

    def __init__(self, h_threshold: float = 0.16, v_threshold: float = 0.10,
                 adapt: float = 0.005, iris_weight: float = 0.0, head_weight: float = 0.0,
                 iris_v_weight: float = 0.0, head_v_weight: float = 0.0):
        self.h_threshold = h_threshold
        self.v_threshold = v_threshold
        self.adapt_rate = adapt
        self.weights = {"iris_weight": iris_weight, "head_weight": head_weight,
                        "iris_v_weight": iris_v_weight, "head_v_weight": head_v_weight}
        self.centre = None  # (h, v) when looking at the middle of the screen
        self._last: dict = {}

    def _hv(self, feature):
        return combine(signals(feature), **self.weights)

    def set_centre(self, features) -> None:
        """Learn "centre" from a second or two of frames looking at the screen."""
        pairs = [self._hv(f) for f in features]
        if not pairs:
            raise ValueError("no frames to learn the centre from")
        self.centre = (sum(p[0] for p in pairs) / len(pairs),
                       sum(p[1] for p in pairs) / len(pairs))

    def last(self) -> dict:
        """The most recent reading, for a live readout: how far the eyes are
        from centre on each axis, in units of the threshold (1.0 = just enough)."""
        return dict(self._last)

    def read(self, feature) -> str | None:
        if self.centre is None or feature is None:
            self._last = {}
            return None
        s = signals(feature)
        h, v = combine(s, **self.weights)
        dh, dv = h - self.centre[0], v - self.centre[1]
        self._last = {"side": round(dh / self.h_threshold, 2), "up": round(dv / self.v_threshold, 2),
                      "head_yaw": round(s["head_yaw"], 3)}
        if abs(dh) >= self.h_threshold:
            return LEFT if dh > 0 else RIGHT
        if dv >= self.v_threshold:
            return UP
        return CENTRE

    def adapt(self, feature) -> None:
        """Follow slow drift: nudge the centre toward a frame already read as
        centre. Call only for such frames."""
        if self.centre is None or feature is None:
            return
        h, v = self._hv(feature)
        a = self.adapt_rate
        self.centre = (self.centre[0] + a * (h - self.centre[0]),
                       self.centre[1] + a * (v - self.centre[1]))
