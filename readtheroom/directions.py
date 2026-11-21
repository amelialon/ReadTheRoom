from __future__ import annotations

"""Coarse gaze direction (left / right / up / down / centre) with no trained
calibration: only one look at the centre is needed, to learn where "centre"
is for this person and camera position. After GaVe (Zeng et al., 2023).

The thresholds come from one person on one laptop camera (see
`python -m readtheroom.check_directions`), so re-measure them on the demo
hardware. Two things that measurement showed:
- Looking down cannot be told from half-closed eyes, so "down" is not used.
- Looking sideways also lifts the up/down signal, so sideways is decided first.
"""

# Positions in the feature vector from features.py
IN_L, OUT_L, UP_L, DOWN_L, IN_R, OUT_R, UP_R, DOWN_R = range(8)
HEAD_YAW, HEAD_PITCH = 12, 13

CENTRE, LEFT, RIGHT, UP, DOWN = "centre", "left", "right", "up", "down"


def signals(feature) -> dict:
    """Named direction signals from one feature vector.

    eye_h > 0 means the eyes are turned toward the patient's own left;
    eye_v > 0 means up. Head angles are passed through unchanged, because
    their sign depends on the camera and is measured by the experiment.
    """
    f = feature
    return {
        "eye_h": float((f[OUT_L] + f[IN_R]) / 2 - (f[IN_L] + f[OUT_R]) / 2),
        "eye_v": float((f[UP_L] + f[UP_R]) / 2 - (f[DOWN_L] + f[DOWN_R]) / 2),
        "head_yaw": float(f[HEAD_YAW]),
        "head_pitch": float(f[HEAD_PITCH]),
    }


class DirectionReader:
    """Turns signals into left / right / up / centre, relative to a learned centre."""

    def __init__(self, h_threshold: float = 0.16, v_threshold: float = 0.10,
                 adapt: float = 0.005):
        self.h_threshold = h_threshold
        self.v_threshold = v_threshold
        self.adapt_rate = adapt
        self.centre = None  # (h, v) when looking at the middle of the screen
        self._last: dict = {}

    @staticmethod
    def _hv(feature):
        s = signals(feature)
        return s["eye_h"], s["eye_v"]

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
        h, v = self._hv(feature)
        dh, dv = h - self.centre[0], v - self.centre[1]
        s = signals(feature)
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
