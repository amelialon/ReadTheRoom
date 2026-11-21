from __future__ import annotations


class Calibrator:
    """Walks the patient through 'look at X' for each target and records features.

    Each target gets a settle period (eyes travel there) then a record period.
    """

    def __init__(self, targets, settle_s: float, record_s: float, min_samples: int = 10):
        self.targets = list(targets)  # [(zone_id, instruction), ...]
        self.settle_s = settle_s
        self.record_s = record_s
        self.min_samples = min_samples
        self.active = False
        self.error: str | None = None
        self.X: list = []
        self.y: list = []

    def start(self, now: float) -> None:
        self.active = True
        self.error = None
        self.X, self.y = [], []
        self._index = 0
        self._target_since = now
        self._count = 0

    def update(self, now: float, feature) -> bool:
        """Feed one frame's features (or None). Returns True when finished."""
        if not self.active:
            return False
        elapsed = now - self._target_since
        zone_id, instruction = self.targets[self._index]
        if elapsed >= self.settle_s + self.record_s:
            if self._count < self.min_samples:
                self.error = (f"Could not see the eyes clearly during "
                              f"'{instruction}'. Check lighting and camera, then retry.")
                self.active = False
                return True
            self._index += 1
            self._target_since = now
            self._count = 0
            if self._index >= len(self.targets):
                self.active = False
                return True
            return False
        if elapsed >= self.settle_s and feature is not None:
            self.X.append(feature)
            self.y.append(zone_id)
            self._count += 1
        return False

    def snapshot(self, now: float) -> dict:
        if not self.active:
            return {"active": False, "error": self.error}
        zone_id, instruction = self.targets[self._index]
        elapsed = now - self._target_since
        recording = elapsed >= self.settle_s
        progress = (elapsed - self.settle_s) / self.record_s if recording else 0.0
        return {"active": True, "error": None, "target": zone_id,
                "instruction": instruction, "recording": recording,
                "progress": round(max(0.0, min(1.0, progress)), 3),
                "step": self._index + 1, "steps": len(self.targets)}
