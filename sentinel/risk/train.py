"""Train and calibrate the risk model from benchmark feature rows.

    python -m sentinel.risk.train --data bench/out/features.jsonl --out sentinel/risk/artifacts/model.json

Each input row is ``{"id", "repo", "created_at", "label", "features": {...}}``. Splits are made
by time (oldest 60% train, next 20% validation for calibration and thresholds, newest 20% test)
and never at random, so a model is never evaluated on PRs older than those it learned from.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from sentinel.risk.calibration import brier, ece
from sentinel.risk.features import GROUPS
from sentinel.risk.model import train
from sentinel.risk.policy import choose_thresholds


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def time_split(rows: list[dict], train_frac: float = 0.6, val_frac: float = 0.2) -> tuple[list[dict], list[dict], list[dict]]:
    ordered = sorted(rows, key=lambda r: (r.get("created_at", ""), r["id"]))
    n = len(ordered)
    a, b = int(n * train_frac), int(n * (train_frac + val_frac))
    return ordered[:a], ordered[a:b], ordered[b:]


def repo_split(rows: list[dict], test_repo: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Leave-one-repository-out: train/validate on the others, test on ``test_repo``."""
    others = [r for r in rows if r.get("repo") != test_repo]
    held_out = [r for r in rows if r.get("repo") == test_repo]
    train_rows, val_rows, extra = time_split(others, 0.75, 0.25)
    return train_rows, val_rows + extra, held_out


def metrics(labels, probs, threshold: float | None = None) -> dict:
    labels, probs = np.asarray(labels, int), np.asarray(probs, float)
    out = {"n": int(len(labels)), "positives": int(labels.sum())}
    if len(set(labels.tolist())) == 2:
        out["roc_auc"] = round(float(roc_auc_score(labels, probs)), 4)
        out["pr_auc"] = round(float(average_precision_score(labels, probs)), 4)
        out["recall_at_5fpr"] = round(recall_at_fpr(labels, probs, 0.05), 4)
    out["brier"] = round(brier(labels, probs), 4)
    out["ece"] = round(ece(labels, probs), 4)
    if threshold is not None:
        predicted = probs >= threshold
        out["false_block_rate"] = round(float(predicted[labels == 0].mean()) if (labels == 0).any() else 0.0, 4)
        out["block_recall"] = round(float(predicted[labels == 1].mean()) if (labels == 1).any() else 0.0, 4)
    return out


def recall_at_fpr(labels, probs, fpr: float) -> float:
    labels, probs = np.asarray(labels, int), np.asarray(probs, float)
    benign = probs[labels == 0]
    if not len(benign):
        return float("nan")
    for t in np.unique(np.concatenate([probs, [1.0 + 1e-9]])):
        if np.mean(benign >= t) <= fpr:
            return float(np.mean(probs[labels == 1] >= t))
    return 0.0


def fit_and_evaluate(rows: list[dict], *, kind: str = "logistic", group: str = "full", split: str = "time", test_repo: str | None = None) -> tuple[object, dict]:
    train_rows, val_rows, test_rows = repo_split(rows, test_repo) if split == "repo" and test_repo else time_split(rows)
    names = GROUPS[group]
    model = train(
        [r["features"] for r in train_rows], [r["label"] for r in train_rows],
        kind=kind, feature_names=names,
        validation=([r["features"] for r in val_rows], [r["label"] for r in val_rows]),
        name=f"{kind}-{group}",
    )
    val_probs = [model.predict(r["features"]) for r in val_rows]
    model.thresholds = choose_thresholds(val_probs, [r["label"] for r in val_rows])
    test_probs = [model.predict(r["features"]) for r in test_rows]
    report = {
        "split": split, "kind": kind, "group": group,
        "sizes": {"train": len(train_rows), "validation": len(val_rows), "test": len(test_rows)},
        "thresholds": model.thresholds,
        "validation": metrics([r["label"] for r in val_rows], val_probs, model.thresholds["block"]),
        "test": metrics([r["label"] for r in test_rows], test_probs, model.thresholds["block"]),
    }
    model.metadata["evaluation"] = report
    return model, report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train the SentinelPR risk model")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("sentinel/risk/artifacts/model.json"))
    parser.add_argument("--kind", choices=["logistic", "lightgbm"], default="logistic")
    parser.add_argument("--group", choices=sorted(GROUPS), default="full")
    args = parser.parse_args(argv)
    rows = load_rows(args.data)
    model, report = fit_and_evaluate(rows, kind=args.kind, group=args.group)
    model.save(args.out)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
