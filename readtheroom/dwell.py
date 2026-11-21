from __future__ import annotations

from collections import Counter, deque


class DwellDetector:
    """Declares a winner when one label holds most of a sliding time window.

    Never acts on single frames: a label wins only if it makes up at least
    `win_fraction` of the samples seen over a full `window_s`.
    """

    def __init__(self, window_s: float, win_fraction: float, min_samples: int = 3):
        self.window_s = window_s
        self.win_fraction = win_fraction
        self.min_samples = min_samples
        # A pause this long in the samples (stalled camera, calibration) means
        # the hold was not observed continuously, so it starts again.
        self.max_gap_s = window_s / 2
        self._samples: deque = deque()
        self._start: float | None = None

    def reset(self) -> None:
        self._samples.clear()
        self._start = None

    def add(self, now: float, label: str | None) -> None:
        if self._samples and now - self._samples[-1][0] > self.max_gap_s:
            self.reset()
        if self._start is None:
            self._start = now
        self._samples.append((now, label))
        self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _leader(self, now: float):
        """Returns (label, share of samples, share of the window observed)."""
        self._prune(now)
        if self._samples and now - self._samples[-1][0] > self.max_gap_s:
            self.reset()
        if self._start is None or not self._samples:
            return None, 0.0, 0.0
        counts = Counter(lbl for _, lbl in self._samples if lbl is not None)
        if not counts:
            return None, 0.0, 0.0
        label, n = counts.most_common(1)[0]
        coverage = min(1.0, (now - self._start) / self.window_s)
        return label, n / len(self._samples), coverage

    def winner(self, now: float) -> str | None:
        label, share, coverage = self._leader(now)
        if label is None or coverage < 1.0 or len(self._samples) < self.min_samples:
            return None
        return label if share >= self.win_fraction else None

    def progress(self, now: float):
        """(leading label, 0..1 progress toward winning) for on-screen feedback."""
        label, share, coverage = self._leader(now)
        if label is None:
            return None, 0.0
        return label, min(1.0, share * coverage / self.win_fraction)
