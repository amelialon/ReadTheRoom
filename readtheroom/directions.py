from __future__ import annotations

"""Coarse gaze direction (left / right / up / down / centre) with no trained
calibration: only one look at the centre is needed, to learn where "centre"
is for this person and camera position. After GaVe (Zeng et al., 2023).

EXPERIMENTAL: the thresholds below are first guesses and are not used by the
app yet. Run `python -m readtheroom.check_directions` to measure them.
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
    """Turns signals into one of five directions, relative to a learned centre."""

    def __init__(self, h_threshold: float = 0.18, v_threshold: float = 0.15,
                 head_h_weight: float = 0.0, head_v_weight: float = 0.0):
        self.h_threshold = h_threshold
        self.v_threshold = v_threshold
        self.head_h_weight = head_h_weight
        self.head_v_weight = head_v_weight
        self.centre = None  # (h, v) when looking at the middle of the screen

    def _hv(self, feature):
        s = signals(feature)
        return (s["eye_h"] + self.head_h_weight * s["head_yaw"],
                s["eye_v"] + self.head_v_weight * s["head_pitch"])

    def set_centre(self, features) -> None:
        """Learn "centre" from a second or two of frames looking at the screen."""
        pairs = [self._hv(f) for f in features]
        if not pairs:
            raise ValueError("no frames to learn the centre from")
        self.centre = (sum(p[0] for p in pairs) / len(pairs),
                       sum(p[1] for p in pairs) / len(pairs))

    def read(self, feature) -> str | None:
        if self.centre is None or feature is None:
            return None
        h, v = self._hv(feature)
        dh, dv = h - self.centre[0], v - self.centre[1]
        # Compare each axis in units of its own threshold, so the stronger
        # movement wins when the eyes go diagonally.
        nh, nv = dh / self.h_threshold, dv / self.v_threshold
        if abs(nh) < 1 and abs(nv) < 1:
            return CENTRE
        if abs(nh) >= abs(nv):
            return LEFT if nh > 0 else RIGHT
        return UP if nv > 0 else DOWN
