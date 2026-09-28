"""The release-risk model.

Two learners are supported and compared in the evaluation:

* ``logistic``  standardised logistic regression. Interpretable (coefficient x standardised
  value = contribution) and data-efficient. The default.
* ``lightgbm``  gradient-boosted trees, with SHAP contributions when ``shap`` is installed.

Both are post-hoc calibrated (isotonic or Platt) on a held-out validation split. The whole
model is saved as JSON — no pickles, so an artifact from the repository can never execute code.

Until a model has been trained on benchmark data, :data:`PRIOR` is used: a hand-weighted
logistic model encoding the evidence directions that motivate the design (failures, uncovered
lines and surviving mutants raise risk; past bugs raise risk; test/doc-only changes lower it).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from sentinel.models import Contribution, RiskAssessment
from sentinel.risk.calibration import calibrator_from_dict, fit_calibrator
from sentinel.risk.features import BY_NAME, DIRECTIONS, FEATURE_NAMES, vectorise


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(min(z, 40), -40)))


@dataclass
class RiskModel:
    kind: str
    feature_names: tuple[str, ...]
    intercept: float = 0.0
    coef: list[float] = field(default_factory=list)
    mean: list[float] = field(default_factory=list)
    scale: list[float] = field(default_factory=list)
    booster: str | None = None  # LightGBM model string
    calibrator: dict | None = None
    thresholds: dict[str, float] = field(default_factory=lambda: {"canary": 0.35, "block": 0.65})
    metadata: dict = field(default_factory=dict)

    # scoring --------------------------------------------------------------------
    def _standardise(self, x: np.ndarray) -> np.ndarray:
        mean = np.asarray(self.mean or [0.0] * len(x))
        scale = np.asarray(self.scale or [1.0] * len(x))
        return (x - mean) / np.where(scale == 0, 1, scale)

    def raw_score(self, features: dict[str, float]) -> float:
        x = np.asarray(vectorise(features, self.feature_names), float)
        if self.kind == "lightgbm":
            return float(self._lgb().predict(x.reshape(1, -1))[0])
        return _sigmoid(self.intercept + float(np.dot(self.coef, self._standardise(x))))

    def predict(self, features: dict[str, float]) -> float:
        score = self.raw_score(features)
        calibrator = calibrator_from_dict(self.calibrator)
        return round(calibrator.apply(score) if calibrator else score, 4)

    def contributions(self, features: dict[str, float]) -> list[Contribution]:
        x = np.asarray(vectorise(features, self.feature_names), float)
        if self.kind == "lightgbm":
            values = self._tree_contributions(x)
        else:
            values = np.asarray(self.coef) * self._standardise(x)
        rows = [
            Contribution(feature=n, value=round(float(v), 4), contribution=round(float(c), 4), label=BY_NAME[n].label if n in BY_NAME else n)
            for n, v, c in zip(self.feature_names, x, values)
        ]
        return sorted(rows, key=lambda r: abs(r.contribution), reverse=True)

    def assess(self, features: dict[str, float]) -> RiskAssessment:
        return RiskAssessment(
            probability=self.predict(features),
            model=f"{self.kind}:{self.metadata.get('name', 'unnamed')}",
            calibrated=self.calibrator is not None,
            contributions=self.contributions(features)[:8],
            features={k: round(float(v), 4) for k, v in features.items()},
        )

    # LightGBM helpers --------------------------------------------------------------
    def _lgb(self):
        if not hasattr(self, "_booster_obj"):
            import lightgbm as lgb

            self._booster_obj = lgb.Booster(model_str=self.booster)
        return self._booster_obj

    def _tree_contributions(self, x: np.ndarray) -> np.ndarray:
        try:
            import shap

            explainer = shap.TreeExplainer(self._lgb())
            return np.asarray(explainer.shap_values(x.reshape(1, -1)))[0]
        except Exception:
            contrib = self._lgb().predict(x.reshape(1, -1), pred_contrib=True)[0]
            return np.asarray(contrib[:-1])

    # persistence --------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "feature_names": list(self.feature_names), "intercept": self.intercept,
            "coef": self.coef, "mean": self.mean, "scale": self.scale, "booster": self.booster,
            "calibrator": self.calibrator, "thresholds": self.thresholds, "metadata": self.metadata,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def from_dict(cls, data: dict) -> "RiskModel":
        data = dict(data)
        data["feature_names"] = tuple(data["feature_names"])
        return cls(**data)

    @classmethod
    def load(cls, path: Path | str | None) -> "RiskModel":
        if path and Path(path).exists():
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        return PRIOR


# Hand-weighted prior, in raw (unstandardised) feature units.
_PRIOR_WEIGHTS = {
    "verified_test_failures": 3.0,
    "uncovered_changed_lines": 0.18,
    "surviving_mutants": 0.45,
    "szz_defects": 0.55,
    "verified_bug_links": 0.35,
    "verified_api_breaks": 1.2,
    "log_churn": 0.35,
    "entropy": 0.4,
    "author_is_new": 0.25,
    "touches_dependencies": 0.9,
    "touches_ci": 0.6,
    "signature_changes": 0.4,
    "injection_attempts": 0.6,
    "llm_risk": 0.8,
    "changed_line_coverage": -1.4,
    "mutation_score": -1.2,
    "test_only": -1.8,
    "docs_only": -2.5,
}
PRIOR = RiskModel(
    kind="logistic",
    feature_names=FEATURE_NAMES,
    intercept=-0.9,
    coef=[_PRIOR_WEIGHTS.get(n, 0.0) for n in FEATURE_NAMES],
    mean=[0.0] * len(FEATURE_NAMES),
    scale=[1.0] * len(FEATURE_NAMES),
    metadata={"name": "prior", "note": "hand-weighted prior; replace by training on benchmark data"},
)


# ---------------------------------------------------------------------------
# training
# ---------------------------------------------------------------------------


def fit_monotone_logistic(X: np.ndarray, y: np.ndarray, directions: list[int], l2: float = 2.0) -> tuple[float, list[float]]:
    """Class-balanced, L2-regularised logistic regression whose coefficients respect a sign.

    ``directions[i]`` is +1 (coefficient >= 0), -1 (<= 0) or 0 (free). With a few hundred
    training PRs an unconstrained model happily learns that, say, more uncovered lines make a
    change *safer*; the constraint rules such small-sample artefacts out while leaving the
    magnitudes to the data. Solved with L-BFGS-B, where the signs are simple bounds.
    """
    from scipy.optimize import minimize

    n, d = X.shape
    pos = max(int(y.sum()), 1)
    weights = np.where(y == 1, n / (2 * pos), n / (2 * max(n - pos, 1)))

    def loss(params: np.ndarray) -> tuple[float, np.ndarray]:
        b, w = params[0], params[1:]
        z = b + X @ w
        p = 1 / (1 + np.exp(-np.clip(z, -35, 35)))
        eps = 1e-12
        nll = -np.sum(weights * (y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))) / n
        grad_z = weights * (p - y) / n
        grad = np.concatenate([[grad_z.sum()], X.T @ grad_z + l2 * w / n])
        return nll + 0.5 * l2 * float(w @ w) / n, grad

    bounds = [(None, None)] + [(0, None) if s > 0 else (None, 0) if s < 0 else (None, None) for s in directions]
    result = minimize(loss, np.zeros(d + 1), jac=True, method="L-BFGS-B", bounds=bounds)
    return float(result.x[0]), [float(c) for c in result.x[1:]]


def train(
    rows: list[dict[str, float]],
    labels: list[int],
    *,
    kind: str = "logistic",
    feature_names: tuple[str, ...] = FEATURE_NAMES,
    validation: tuple[list[dict[str, float]], list[int]] | None = None,
    calibration: str = "auto",
    name: str = "trained",
    seed: int = 7,
    monotone: bool = True,
    l2: float = 2.0,
) -> RiskModel:
    """Fit on ``rows`` and calibrate on ``validation`` (a separate split, never the training data)."""
    X = np.asarray([vectorise(r, feature_names) for r in rows], float)
    y = np.asarray(labels, int)
    if kind == "lightgbm":
        import lightgbm as lgb

        booster = lgb.train(
            {"objective": "binary", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 10,
             "feature_fraction": 0.9, "bagging_fraction": 0.9, "bagging_freq": 1, "verbose": -1, "seed": seed},
            lgb.Dataset(X, label=y, feature_name=list(feature_names)),
            num_boost_round=200,
        )
        model = RiskModel(kind="lightgbm", feature_names=feature_names, booster=booster.model_to_string())
    else:
        mean = X.mean(axis=0)
        scale = X.std(axis=0)
        scale[scale == 0] = 1.0
        Xs = (X - mean) / scale
        if monotone:
            intercept, coef = fit_monotone_logistic(Xs, y, [DIRECTIONS.get(n, 0) for n in feature_names], l2=l2)
        else:
            from sklearn.linear_model import LogisticRegression

            clf = LogisticRegression(C=1.0 / l2, class_weight="balanced", max_iter=2000, random_state=seed)
            clf.fit(Xs, y)
            intercept, coef = float(clf.intercept_[0]), [float(c) for c in clf.coef_[0]]
        model = RiskModel(kind="logistic", feature_names=feature_names, intercept=intercept, coef=coef,
                          mean=mean.tolist(), scale=scale.tolist())
    model.metadata = {"name": name, "train_size": int(len(y)), "positives": int(y.sum()), "monotone": monotone and kind == "logistic"}
    if validation is not None and validation[0]:
        scores = [model.raw_score(r) for r in validation[0]]
        calibrator = fit_calibrator(scores, validation[1], calibration)
        model.calibrator = calibrator.to_dict() if calibrator else None
        model.metadata["validation_size"] = len(validation[1])
    return model
