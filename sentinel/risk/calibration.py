"""Probability quality: Brier score, expected calibration error, reliability curves, and the
calibrators themselves (isotonic and Platt), exported as plain numbers."""

from __future__ import annotations

import numpy as np


def brier(y_true, y_prob) -> float:
    y_true, y_prob = np.asarray(y_true, float), np.asarray(y_prob, float)
    return float(np.mean((y_prob - y_true) ** 2)) if len(y_true) else float("nan")


def reliability(y_true, y_prob, bins: int = 10) -> list[dict]:
    y_true, y_prob = np.asarray(y_true, float), np.asarray(y_prob, float)
    edges = np.linspace(0, 1, bins + 1)
    rows = []
    for i in range(bins):
        mask = (y_prob >= edges[i]) & ((y_prob < edges[i + 1]) if i < bins - 1 else (y_prob <= edges[i + 1]))
        if mask.any():
            rows.append({"bin": i, "lo": float(edges[i]), "hi": float(edges[i + 1]), "count": int(mask.sum()),
                         "mean_pred": float(y_prob[mask].mean()), "frac_pos": float(y_true[mask].mean())})
    return rows


def ece(y_true, y_prob, bins: int = 10) -> float:
    rows = reliability(y_true, y_prob, bins)
    n = sum(r["count"] for r in rows)
    return float(sum(r["count"] / n * abs(r["mean_pred"] - r["frac_pos"]) for r in rows)) if n else float("nan")


class Isotonic:
    """Monotone step calibrator stored as breakpoints; applied with linear interpolation."""

    kind = "isotonic"

    def __init__(self, x: list[float], y: list[float]):
        self.x, self.y = x, y

    @classmethod
    def fit(cls, scores, labels) -> "Isotonic":
        from sklearn.isotonic import IsotonicRegression

        model = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
        model.fit(np.asarray(scores, float), np.asarray(labels, float))
        return cls([float(v) for v in model.X_thresholds_], [float(v) for v in model.y_thresholds_])

    def apply(self, score: float) -> float:
        if not self.x:
            return score
        return float(np.interp(score, self.x, self.y))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "x": self.x, "y": self.y}


class Platt:
    """Logistic calibration: p = sigmoid(a * logit(s) + b)."""

    kind = "platt"

    def __init__(self, a: float, b: float):
        self.a, self.b = a, b

    @staticmethod
    def _logit(p):
        p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p))

    @classmethod
    def fit(cls, scores, labels) -> "Platt":
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(C=1e6)
        model.fit(cls._logit(scores).reshape(-1, 1), np.asarray(labels, int))
        return cls(float(model.coef_[0][0]), float(model.intercept_[0]))

    def apply(self, score: float) -> float:
        z = self.a * float(self._logit([score])[0]) + self.b
        return float(1 / (1 + np.exp(-z)))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "a": self.a, "b": self.b}


def calibrator_from_dict(data: dict | None):
    if not data:
        return None
    if data["kind"] == "isotonic":
        return Isotonic(data["x"], data["y"])
    return Platt(data["a"], data["b"])


def fit_calibrator(scores, labels, method: str = "auto"):
    """Isotonic needs data; with fewer than ~60 validation samples Platt is the safer choice."""
    labels = np.asarray(labels, int)
    if len(set(labels.tolist())) < 2:
        return None
    if method == "isotonic" or (method == "auto" and len(labels) >= 60):
        return Isotonic.fit(scores, labels)
    return Platt.fit(scores, labels)
