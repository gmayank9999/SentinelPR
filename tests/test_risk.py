import random

import numpy as np
import pytest

from sentinel.models import RiskAssessment
from sentinel.risk.calibration import Isotonic, Platt, brier, ece, fit_calibrator, reliability
from sentinel.risk.explain import template_explanation
from sentinel.risk.features import FEATURE_NAMES, GROUPS, build_features
from sentinel.risk.model import PRIOR, RiskModel, train
from sentinel.risk.policy import choose_thresholds, decide
from sentinel.risk.train import fit_and_evaluate, recall_at_fpr, time_split


def synthetic(n=300, seed=1):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        defective = rng.random() < 0.4
        f = build_features({"log_churn": rng.uniform(0, 5), "szz_defects": rng.choice([0, 0, 1, 2]) if defective else rng.choice([0, 0, 0, 1])})
        f["mutation_score"] = rng.uniform(0.0, 0.6) if defective else rng.uniform(0.4, 1.0)
        f["surviving_mutants"] = rng.randint(1, 6) if defective else rng.randint(0, 2)
        f["changed_line_coverage"] = rng.uniform(0.3, 1.0)
        rows.append({"id": f"pr{i}", "created_at": f"2026-01-{1 + i % 28:02d}T{i % 24:02d}", "label": int(defective), "features": f})
    return rows


def test_build_features_defaults_and_signals():
    f = build_features({"log_churn": 2.0, "author_prior_commits": 3, "code_age_days": 10})
    assert set(f) == set(FEATURE_NAMES)
    assert f["mutation_score"] == 1.0 and f["changed_line_coverage"] == 1.0 and f["llm_risk"] == 0.5
    assert f["log_author_prior_commits"] == pytest.approx(np.log1p(3))
    assert set(GROUPS["jit"]) < set(FEATURE_NAMES)
    assert "mutation_score" not in GROUPS["no_mutation"]


def test_prior_model_direction():
    safe = build_features({"log_churn": 1.0, "docs_only": 1.0})
    risky = dict(safe, docs_only=0.0, verified_test_failures=0.0, surviving_mutants=5.0, mutation_score=0.2, szz_defects=2.0)
    assert PRIOR.predict(risky) > PRIOR.predict(safe)
    assert PRIOR.contributions(risky)[0].feature in {"surviving_mutants", "szz_defects", "mutation_score"}
    failing = dict(risky, verified_test_failures=2.0)
    assert PRIOR.predict(failing) > PRIOR.predict(risky)


def test_train_calibrate_roundtrip(tmp_path):
    rows = synthetic()
    train_rows, val_rows, test_rows = time_split(rows)
    model = train([r["features"] for r in train_rows], [r["label"] for r in train_rows],
                  validation=([r["features"] for r in val_rows], [r["label"] for r in val_rows]))
    probs = [model.predict(r["features"]) for r in test_rows]
    labels = [r["label"] for r in test_rows]
    assert np.mean([p for p, y in zip(probs, labels) if y]) > np.mean([p for p, y in zip(probs, labels) if not y])
    model.save(tmp_path / "m.json")
    loaded = RiskModel.load(tmp_path / "m.json")
    assert loaded.predict(test_rows[0]["features"]) == model.predict(test_rows[0]["features"])
    assert RiskModel.load(tmp_path / "missing.json") is PRIOR


def test_fit_and_evaluate_report():
    model, report = fit_and_evaluate(synthetic(), group="full")
    assert report["test"]["roc_auc"] > 0.8
    assert 0 < report["thresholds"]["canary"] <= report["thresholds"]["block"] <= 1
    assert report["validation"]["false_block_rate"] <= 0.05 + 1e-9
    _, jit = fit_and_evaluate(synthetic(), group="jit")
    assert jit["test"]["roc_auc"] < report["test"]["roc_auc"]


def test_calibration_metrics():
    y = [0, 0, 1, 1]
    assert brier(y, [0, 0, 1, 1]) == 0
    assert ece(y, [0.5] * 4) == pytest.approx(0.0)
    assert ece(y, [0.9, 0.9, 0.9, 0.9]) == pytest.approx(0.4)
    assert reliability(y, [0.1, 0.2, 0.8, 0.9], bins=2)[1]["frac_pos"] == 1.0
    iso = Isotonic.fit([0.1, 0.2, 0.3, 0.8, 0.9], [0, 0, 1, 1, 1])
    assert iso.apply(0.05) < iso.apply(0.95)
    platt = Platt.fit([0.1, 0.2, 0.3, 0.8, 0.9], [0, 0, 1, 1, 1])
    assert platt.apply(0.1) < platt.apply(0.9)
    assert fit_calibrator([0.1, 0.2], [1, 1]) is None


def test_policy():
    t = {"canary": 0.3, "block": 0.7}
    assert decide(0.1, 0, t).decision == "PASS"
    assert decide(0.5, 0, t).decision == "CANARY"
    assert decide(0.8, 0, t).decision == "BLOCK"
    assert decide(0.05, 1, t).decision == "BLOCK"


def test_choose_thresholds_respects_false_block_budget():
    rng = np.random.default_rng(0)
    benign = rng.uniform(0, 0.6, 200)
    defective = rng.uniform(0.3, 1.0, 200)
    scores = np.concatenate([benign, defective])
    labels = np.array([0] * 200 + [1] * 200)
    t = choose_thresholds(scores, labels)
    assert np.mean(benign >= t["block"]) <= 0.05
    assert np.mean(defective >= t["canary"]) >= 0.9
    assert recall_at_fpr(labels, scores, 0.05) > 0.3


def test_template_explanation_cites_features():
    assessment = PRIOR.assess(dict(build_features({}), surviving_mutants=4.0, mutation_score=0.2))
    text = template_explanation(assessment)
    assert "[feat:surviving_mutants]" in text and "raises" in text
    assert isinstance(assessment, RiskAssessment)
