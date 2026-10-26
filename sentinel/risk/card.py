"""A model card for the deployed risk model: what it is, what it was fitted on, how it was evaluated.

Everything is read from the model file and the committed benchmark results, never typed in, so
the product shows exactly what the evaluation supports.
"""

from __future__ import annotations

import json
from pathlib import Path

from sentinel.config import PACKAGE_DIR, Config
from sentinel.risk.features import BY_NAME, DIRECTIONS, FEATURES
from sentinel.risk.model import RiskModel

BUNDLED = PACKAGE_DIR / "risk" / "artifacts" / "model.json"


def _evaluations(root: Path) -> list[dict]:
    rows = []
    for path in sorted((root / "bench" / "results").glob("*/results.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            table = data["gate"]["table"]
            ours = table["SentinelPR"]
        except (json.JSONDecodeError, KeyError):
            continue
        tests = table.get("B-tests", {})
        jit = table.get("B-JIT", {})
        rows.append({
            "repo": data.get("repo") or path.parent.name, "prs": data["dataset"]["prs"], "scored_out_of_fold": data["gate"].get("n_scored"),
            "selected_family": data["gate"].get("selected_family"),
            "sentinel": {k: ours.get(k) for k in ("pr_auc", "roc_auc", "recall_at_5fpr", "brier", "ece", "false_block_rate", "block_recall")},
            "merge_if_tests_pass": {k: tests.get(k) for k in ("pr_auc", "roc_auc", "brier", "false_block_rate", "block_recall")},
            "jit": {k: jit.get(k) for k in ("pr_auc", "roc_auc", "brier", "false_block_rate", "block_recall")},
            "source": path.relative_to(root).as_posix(),
        })
    return rows


def model_card(cfg: Config) -> dict:
    model = RiskModel.load(BUNDLED)
    meta = model.metadata
    expert = meta.get("family") == "expert"
    features = []
    for name, coef, scale in zip(model.feature_names, model.coef or [0.0] * len(model.feature_names), model.scale or [1.0] * len(model.feature_names)):
        f = BY_NAME[name]
        features.append({"name": name, "group": f.group, "label": f.label, "direction": DIRECTIONS.get(name, 0), "coefficient": round(coef, 4),
                         "per_unit": round(coef / (scale or 1.0), 4), "default": f.default})
    return {
        "name": meta.get("name"), "kind": model.kind, "family": meta.get("family", "fitted"), "trained_at": meta.get("trained_at"),
        "weights_origin": ("hand-set expert weights (written before the benchmark existed); only a 2-parameter Platt calibration was fitted"
                           if expert else "coefficients fitted on benchmark PRs"),
        "calibration": {"kind": (model.calibrator or {}).get("kind"), "pr_count": meta.get("calibration_size"), "positives": meta.get("positives"),
                        "calibrated_on": meta.get("calibration_base"), "fitted_on": meta.get("fit_bases")},
        "thresholds": model.thresholds, "threshold_rule": "chosen on the calibration release so that at most 5% of benign PRs are blocked",
        "features": features, "feature_count": len(FEATURES),
        "evaluations": _evaluations(cfg.repo_root),
        "limitations": [
            "The weights are hand-set, not learned. On the small benchmark, learned weights did not beat them out of fold, so training contributes calibration and thresholds, not ranking (docs/evaluation.md).",
            "The decision to deploy the hand-set weights was made after seeing benchmark results, so the headline numbers carry some selection optimism.",
            "Thresholds were chosen on UniERP. On a real repository (humanize) they gave a false-block rate above the 5% target; thresholds must be re-chosen per repository.",
            "Most benchmark defects are generated (mutations, reverted fixes, signature changes); they approximate but are not real-world defects.",
            "The 'merge if tests pass' comparison is binary, which flatters the AUC gap; block decisions differ on few PRs.",
            "A calibrated probability is a statement about PRs like the benchmark's, not a guarantee about this PR.",
        ],
        "limitations_source": "docs/evaluation.md",
    }
