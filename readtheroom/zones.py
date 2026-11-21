from __future__ import annotations

import json
from collections import Counter

import numpy as np


class ZoneClassifier:
    """k-nearest-neighbour map from gaze features to a named zone.

    No 3D geometry: calibration records what the patient's eyes and head look
    like for each zone, and prediction finds the closest recorded examples.
    Returns None when the features are far from everything seen in
    calibration, i.e. the patient is looking somewhere unregistered.
    """

    def __init__(self, k: int = 5, reject_factor: float = 1.5):
        self.k = k
        self.reject_factor = reject_factor
        self.fitted = False

    def fit(self, X, y) -> None:
        X = np.asarray(X, dtype=float)
        labels = list(y)
        if X.ndim != 2 or len(X) != len(labels) or len(X) < 2:
            raise ValueError("need a 2-D feature array with one label per row")
        if len(set(labels)) < 2:
            raise ValueError("need at least two zones")
        self._mean = X.mean(axis=0)
        # Scale by noise within a zone, so features that separate zones count
        # for more than features that only jitter.
        within = np.zeros(X.shape[1])
        for label in set(labels):
            rows = X[[l == label for l in labels]]
            within += ((rows - rows.mean(axis=0)) ** 2).sum(axis=0)
        within = np.sqrt(within / len(X))
        self._scale = np.maximum(np.maximum(within, 0.05 * X.std(axis=0)), 1e-6)
        self._Z = (X - self._mean) / self._scale
        self._labels = labels
        self._k = max(1, min(self.k, len(X) - 1))
        d = self._pairwise()
        knn = np.sort(d, axis=1)[:, : self._k].mean(axis=1)
        self._threshold = float(np.percentile(knn, 95) * self.reject_factor)
        self.fitted = True

    def _pairwise(self) -> np.ndarray:
        diff = self._Z[:, None, :] - self._Z[None, :, :]
        d = np.sqrt((diff ** 2).sum(axis=2))
        np.fill_diagonal(d, np.inf)
        return d

    def _vote(self, dists: np.ndarray):
        idx = np.argsort(dists)[: self._k]
        if not np.isfinite(dists[idx]).all() or dists[idx].mean() > self._threshold:
            return None
        counts = Counter(self._labels[i] for i in idx).most_common()
        if len(counts) > 1 and counts[0][1] == counts[1][1]:
            return self._labels[idx[0]]
        return counts[0][0]

    def predict(self, x) -> str | None:
        if not self.fitted or x is None:
            return None
        x = np.asarray(x, dtype=float)
        if x.shape != self._mean.shape or not np.isfinite(x).all():
            return None
        z = (x - self._mean) / self._scale
        return self._vote(np.sqrt(((self._Z - z) ** 2).sum(axis=1)))

    def self_check(self) -> dict:
        """Leave-one-out accuracy per zone on the calibration samples.

        Optimistic: samples are consecutive frames of the same two seconds.
        A low number here still reliably means two zones are too close.
        """
        d = self._pairwise()
        hits: Counter = Counter()
        totals: Counter = Counter()
        for i, label in enumerate(self._labels):
            totals[label] += 1
            hits[label] += self._vote(d[i]) == label
        return {label: round(hits[label] / totals[label], 3) for label in totals}

    def score(self, X, y) -> dict:
        """Share of the given samples recognised as their own zone, per zone."""
        hits: Counter = Counter()
        totals: Counter = Counter()
        for x, label in zip(X, y):
            totals[label] += 1
            hits[label] += self.predict(x) == label
        return {label: round(hits[label] / totals[label], 3) for label in totals}

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"k": self._k, "threshold": self._threshold,
                       "mean": self._mean.tolist(), "scale": self._scale.tolist(),
                       "Z": self._Z.tolist(), "labels": self._labels}, f)

    def load(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self._k = data["k"]
        self._threshold = data["threshold"]
        self._mean = np.asarray(data["mean"])
        self._scale = np.asarray(data["scale"])
        self._Z = np.asarray(data["Z"])
        self._labels = data["labels"]
        self.fitted = True

    @property
    def labels(self) -> set:
        return set(self._labels) if self.fitted else set()
