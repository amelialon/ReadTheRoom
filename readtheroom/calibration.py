from __future__ import annotations

import math

import numpy as np

from .config import ANSWER_MODES, NO, REST, YES


def control_targets(answer_mode: str = "horizontal") -> list:
    """The on-screen targets every calibration starts with."""
    if answer_mode not in ANSWER_MODES:
        raise ValueError(f"answer mode must be one of {', '.join(ANSWER_MODES)}")
    if answer_mode == "vertical":
        return [(REST, "Look at the centre of the screen"),
                (YES, "Look at the YES circle at the top"),
                (NO, "Look at the NO circle at the bottom")]
    return [(REST, "Look at the centre of the screen"),
            (YES, "Look at the YES circle on the left"),
            (NO, "Look at the NO circle on the right")]


class Calibrator:
    """Walks the patient through 'look at X' for each target and records features.

    Each target gets a settle period (eyes travel there) then a record period.
    The whole list is shown `passes` times, so the second look at each target
    can be used to check what was learned from the first.

    With a `steady` function (feature -> gaze direction, or None) the record
    period does not start on the clock but when the eyes have stopped: the
    directions over the last `steady_s` seconds all within `steady_deg` of
    each other. Finding an object on a table takes a different time for
    every person and every object; recording while the eyes are still
    travelling teaches a point somewhere along the way. Eyes still resting
    on the last target pass for stopped too, so a jump during the recording
    throws the recording away and waits again. The wait is capped at
    `wait_s` past the settle period, and a target that hit the cap is listed
    in `unsettled`.
    """

    def __init__(self, targets, settle_s: float, record_s: float,
                 min_samples: int = 8, passes: int = 2, steady=None,
                 wait_s: float = 0.0, steady_deg: float = 3.0, steady_s: float = 0.4):
        self.targets = list(targets) * passes  # [(zone_id, instruction), ...]
        self._per_pass = len(self.targets) // passes
        self.settle_s = settle_s
        self.record_s = record_s
        self.min_samples = min_samples
        self.steady = steady
        self.wait_s = wait_s if steady is not None else 0.0
        self.steady_deg = steady_deg
        self.steady_s = steady_s
        self.active = False
        self.error: str | None = None
        self.X: list = []
        self.y: list = []
        self.passes: list = []  # which pass each sample came from
        self.unsettled: list = []  # targets recorded although the eyes never stopped
        self._record_since: float | None = None
        self._recent: list = []  # (time, direction) since this target came up
        self._rec_dirs: list = []

    def start(self, now: float) -> None:
        self.active = True
        self.error = None
        self.X, self.y, self.passes, self.unsettled = [], [], [], []
        self._index = 0
        self._begin_target(now)

    def _begin_target(self, now: float) -> None:
        self._target_since = now
        self._record_since = None
        self._recent = []
        self._rec_dirs = []  # directions of the frames recorded for this target
        self._count = 0

    def _eyes_stopped(self, now: float) -> bool:
        window = [d for t, d in self._recent if t >= now - self.steady_s]
        if len(window) < 3 or self._recent[0][0] > now - self.steady_s:
            return False  # not a full window yet
        mean = np.mean(window, axis=0)
        norm = np.linalg.norm(mean)
        if norm < 1e-9:
            return False
        mean /= norm
        # Every frame within half the allowance of the mean: the whole window
        # spans about steady_deg at most, so a slow drift does not pass.
        worst = max(math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(d, mean)))))) for d in window)
        return worst < self.steady_deg / 2

    def update(self, now: float, feature) -> bool:
        """Feed one frame's features (or None). Returns True when finished."""
        if not self.active:
            return False
        elapsed = now - self._target_since
        zone_id, instruction = self.targets[self._index]
        if self._record_since is None:
            if self.steady is not None and feature is not None:
                direction = self.steady(feature)
                if direction is not None:
                    self._recent.append((now, np.asarray(direction, dtype=float)))
            if elapsed >= self.settle_s:
                if self.steady is None or self._eyes_stopped(now):
                    self._record_since = now
                elif elapsed >= self.settle_s + self.wait_s:
                    self._record_since = now
                    self.unsettled.append(zone_id)
            if self._record_since is None:
                return False
        if self.steady is not None and feature is not None and elapsed < self.settle_s + self.wait_s:
            # The eyes were resting, but on the last target: when they jump
            # during the recording, drop what was recorded and wait again.
            direction = self.steady(feature)
            if direction is not None and self._count:
                mean = np.mean(self._rec_dirs, axis=0)
                mean /= max(1e-9, np.linalg.norm(mean))
                if math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(direction, mean)))))) > 2 * self.steady_deg:
                    del self.X[-self._count:], self.y[-self._count:], self.passes[-self._count:]
                    self._record_since, self._recent, self._rec_dirs, self._count = None, [(now, np.asarray(direction, dtype=float))], [], 0
                    return False
        if now - self._record_since >= self.record_s:
            if self._count < self.min_samples:
                self.error = (f"Could not see the eyes clearly during "
                              f"'{instruction}'. Check lighting and camera, then retry.")
                self.active = False
                return True
            self._index += 1
            self._begin_target(now)
            if self._index >= len(self.targets):
                self.active = False
                return True
            return False
        if feature is not None:
            self.X.append(feature)
            self.y.append(zone_id)
            self.passes.append(self._index // self._per_pass)
            self._count += 1
            if self.steady is not None:
                direction = self.steady(feature)
                if direction is not None:
                    self._rec_dirs.append(np.asarray(direction, dtype=float))
        return False

    def snapshot(self, now: float) -> dict:
        if not self.active:
            return {"active": False, "error": self.error}
        zone_id, instruction = self.targets[self._index]
        recording = self._record_since is not None
        progress = (now - self._record_since) / self.record_s if recording else 0.0
        return {"active": True, "error": None, "target": zone_id,
                "instruction": instruction, "recording": recording,
                "progress": round(max(0.0, min(1.0, progress)), 3),
                "step": self._index + 1, "steps": len(self.targets)}
