"""Statistics for the benchmark: one function per research question.

Evaluation of the risk gate uses *forward chaining* over base revisions: to score PRs on base
k, a model is trained on bases < k-1, calibrated (and its thresholds chosen) on base k-1, and
applied to base k. Out-of-fold predictions are pooled across test bases. No PR is ever scored
by a model that saw a later revision.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score, roc_curve

from sentinel.risk.calibration import brier, ece, reliability
from sentinel.risk.features import GROUPS
from sentinel.risk.model import PRIOR, calibrated_prior, train
from sentinel.risk.policy import choose_thresholds
from sentinel.risk.train import recall_at_fpr

RNG = np.random.default_rng(4011)


# --- generic helpers -------------------------------------------------------------------

def bootstrap_ci(labels, scores, metric, n: int = 1000) -> tuple[float, float]:
    labels, scores = np.asarray(labels), np.asarray(scores)
    values = []
    for _ in range(n):
        idx = RNG.integers(0, len(labels), len(labels))
        if len(set(labels[idx].tolist())) < 2:
            continue
        values.append(metric(labels[idx], scores[idx]))
    if not values:
        return float("nan"), float("nan")
    return round(float(np.percentile(values, 2.5)), 4), round(float(np.percentile(values, 97.5)), 4)


def mcnemar(correct_a, correct_b) -> dict:
    """Exact (binomial) McNemar test on paired correct/incorrect decisions."""
    a, b = np.asarray(correct_a, bool), np.asarray(correct_b, bool)
    only_a, only_b = int(np.sum(a & ~b)), int(np.sum(~a & b))
    n = only_a + only_b
    if n == 0:
        return {"a_only": 0, "b_only": 0, "p_value": 1.0}
    k = min(only_a, only_b)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return {"a_only": only_a, "b_only": only_b, "p_value": round(min(1.0, 2 * p), 5)}


def wilcoxon(a, b) -> float | None:
    try:
        from scipy.stats import wilcoxon as _w

        diffs = np.asarray(a) - np.asarray(b)
        if not np.any(diffs):
            return 1.0
        return round(float(_w(a, b).pvalue), 5)
    except Exception:
        return None


def score_block(labels, scores, threshold: float) -> dict:
    labels, scores = np.asarray(labels, int), np.asarray(scores, float)
    out = {"n": int(len(labels)), "positives": int(labels.sum())}
    if len(set(labels.tolist())) == 2:
        out.update({
            "roc_auc": round(float(roc_auc_score(labels, scores)), 4),
            "roc_auc_ci": bootstrap_ci(labels, scores, roc_auc_score),
            "pr_auc": round(float(average_precision_score(labels, scores)), 4),
            "pr_auc_ci": bootstrap_ci(labels, scores, average_precision_score),
            "recall_at_5fpr": round(recall_at_fpr(labels, scores, 0.05), 4),
        })
    out["brier"] = round(brier(labels, scores), 4)
    out["ece"] = round(ece(labels, scores), 4)
    blocked = scores >= threshold
    out["threshold"] = round(float(threshold), 4)
    out["false_block_rate"] = round(float(blocked[labels == 0].mean()), 4) if (labels == 0).any() else None
    out["block_recall"] = round(float(blocked[labels == 1].mean()), 4) if (labels == 1).any() else None
    return out


def curves(labels, scores) -> dict:
    labels, scores = np.asarray(labels, int), np.asarray(scores, float)
    if len(set(labels.tolist())) < 2:
        return {}
    fpr, tpr, _ = roc_curve(labels, scores)
    precision, recall, _ = precision_recall_curve(labels, scores)
    return {
        "roc": [[round(float(x), 4), round(float(y), 4)] for x, y in zip(fpr, tpr)],
        "pr": [[round(float(x), 4), round(float(y), 4)] for x, y in zip(recall, precision)],
        "reliability": reliability(labels, scores),
    }


# --- RQ3: the gate ---------------------------------------------------------------------

def _eligible(rows: list[dict]) -> list[dict]:
    """PRs the risk model scores: rejected-by-guard PRs never reach the model."""
    return [r for r in rows if not r["sentinel"]["rejected"] and r["sentinel"]["risk"] is not None]


FAMILIES = {
    "learned": "SentinelPR (learned weights, sign-constrained)",
    "expert": "SentinelPR (expert weights, calibrated)",
}


def fit_family(family: str, train_rows: list[dict], val_rows: list[dict], group: str = "full", kind: str = "logistic", monotone: bool = True):
    """``learned``: fit coefficients on train_rows, calibrate on val_rows.
    ``expert``: keep the expert weights (zeroed outside ``group``), calibrate on train+val rows."""
    if family == "expert":
        model = calibrated_prior(([r["features"] for r in train_rows + val_rows], [r["label"] for r in train_rows + val_rows]))
        keep = set(GROUPS[group])
        model.coef = [c if n in keep else 0.0 for n, c in zip(model.feature_names, model.coef)]
        if group != "full":  # refit the calibrator for the reduced weights
            model = _recalibrate(model, train_rows + val_rows)
        return model
    return train([r["features"] for r in train_rows], [r["label"] for r in train_rows], kind=kind, monotone=monotone,
                 feature_names=GROUPS[group], validation=([r["features"] for r in val_rows], [r["label"] for r in val_rows]))


def _recalibrate(model, rows: list[dict]):
    from sentinel.risk.calibration import fit_calibrator

    model.calibrator = None
    calibrator = fit_calibrator([model.raw_score(r["features"]) for r in rows], [r["label"] for r in rows], "platt")
    model.calibrator = calibrator.to_dict() if calibrator else None
    return model


def forward_chain(rows: list[dict], group: str = "full", kind: str = "logistic", monotone: bool = True, family: str = "learned") -> list[dict]:
    """Out-of-fold predictions: one entry per scored PR on every testable base."""
    rows = _eligible(rows)
    bases = sorted({r["base_order"] for r in rows})
    out = []
    for k in bases[2:]:
        train_rows = [r for r in rows if r["base_order"] < k - 1]
        val_rows = [r for r in rows if r["base_order"] == k - 1]
        test_rows = [r for r in rows if r["base_order"] == k]
        if len({r["label"] for r in train_rows}) < 2:
            continue
        model = fit_family(family, train_rows, val_rows, group, kind, monotone)
        thresholds = choose_thresholds([model.predict(r["features"]) for r in val_rows], [r["label"] for r in val_rows])
        for r in test_rows:
            out.append({"id": r["id"], "label": r["label"], "score": model.predict(r["features"]), "block": thresholds["block"],
                        "canary": thresholds["canary"], "category": r["category"], "tests_fail": 1 - r["tests_pass"]})
    return out


def _decision(p: dict) -> str:
    if p["tests_fail"] or p["score"] >= p["block"]:
        return "BLOCK"
    return "CANARY" if p["score"] >= p["canary"] else "PASS"


def gate_evaluation(rows: list[dict]) -> dict:
    # Model family selection: the family with the best out-of-fold PR-AUC is "SentinelPR".
    candidates = {family: forward_chain(rows, "full", family=family) for family in FAMILIES}

    def pr_auc(preds: list[dict]) -> float:
        labels = [p["label"] for p in preds]
        return average_precision_score(labels, [p["score"] for p in preds]) if len(set(labels)) == 2 else 0.0

    selected = max(candidates, key=lambda f: pr_auc(candidates[f]))
    methods: dict[str, list[dict]] = {"SentinelPR": candidates[selected]}
    for family, preds in candidates.items():
        if family != selected:
            methods[FAMILIES[family]] = preds
    methods["SentinelPR (unconstrained LR)"] = forward_chain(rows, "full", monotone=False)
    methods["B-JIT"] = forward_chain(rows, "jit")
    for name, group in (("ablation: no verification", "no_verification"), ("ablation: no mutation", "no_mutation"),
                        ("ablation: no history", "no_history"), ("ablation: no LLM feature", "no_llm")):
        methods[name] = forward_chain(rows, group, family=selected)
    try:
        import lightgbm  # noqa: F401

        methods["SentinelPR (LightGBM)"] = forward_chain(rows, "full", "lightgbm")
    except ImportError:
        pass
    reference = {p["id"] for p in methods["SentinelPR"]}
    by_id = {r["id"]: r for r in rows}
    # B-tests: merge if the visible tests pass. B-prior: the untrained hand-weighted model.
    methods["B-tests"] = [{"id": i, "label": by_id[i]["label"], "score": float(1 - by_id[i]["tests_pass"]), "block": 0.5, "canary": 0.5,
                           "category": by_id[i]["category"], "tests_fail": 0} for i in sorted(reference)]
    methods["SentinelPR (prior, untrained)"] = [{"id": i, "label": by_id[i]["label"], "score": PRIOR.predict(by_id[i]["features"]),
                                                 "block": 0.65, "canary": 0.35, "category": by_id[i]["category"], "tests_fail": 1 - by_id[i]["tests_pass"]}
                                                for i in sorted(reference)]
    if any("llm_reviewer" in by_id[i] and "probability" in by_id[i]["llm_reviewer"] for i in reference):
        methods["B-LLM"] = [{"id": i, "label": by_id[i]["label"], "score": by_id[i]["llm_reviewer"]["probability"],
                             "block": 0.5, "canary": 0.5, "category": by_id[i]["category"], "tests_fail": 0}
                            for i in sorted(reference) if "probability" in by_id[i].get("llm_reviewer", {})]

    table, curve_data, significance = {}, {}, {}
    for name, preds in methods.items():
        if not preds:
            continue
        labels = [p["label"] for p in preds]
        scores = [p["score"] for p in preds]
        # gate decisions honour the verified-failure rule for SentinelPR variants
        if name.startswith(("SentinelPR", "ablation")):
            blocked = [1.0 if _decision(p) == "BLOCK" else 0.0 for p in preds]
            table[name] = score_block(labels, scores, float(np.median([p["block"] for p in preds])))
            b = np.asarray(blocked, bool)
            y = np.asarray(labels, int)
            table[name]["false_block_rate"] = round(float(b[y == 0].mean()), 4) if (y == 0).any() else None
            table[name]["block_recall"] = round(float(b[y == 1].mean()), 4) if (y == 1).any() else None
        else:
            table[name] = score_block(labels, scores, preds[0]["block"])
        curve_data[name] = curves(labels, scores)

    main = {p["id"]: p for p in methods["SentinelPR"]}
    for name, preds in methods.items():
        if name == "SentinelPR" or not preds:
            continue
        shared = [p for p in preds if p["id"] in main]
        ours = [(_decision(main[p["id"]]) == "BLOCK") == bool(p["label"]) for p in shared]
        theirs = [((p["score"] >= p["block"]) if not name.startswith(("SentinelPR", "ablation")) else _decision(p) == "BLOCK") == bool(p["label"]) for p in shared]
        significance[name] = mcnemar(ours, theirs)

    per_category: dict[str, dict] = defaultdict(lambda: {"n": 0, "defective": 0, "PASS": 0, "CANARY": 0, "BLOCK": 0, "tests_pass_merged_defects": 0})
    for p in methods["SentinelPR"]:
        entry = per_category[p["category"]]
        entry["n"] += 1
        entry["defective"] += p["label"]
        entry[_decision(p)] += 1
    for r in rows:
        if r["label"] == 1 and r["tests_pass"]:
            per_category[r["category"]]["tests_pass_merged_defects"] += 1
    return {"table": table, "curves": curve_data, "mcnemar_vs_sentinel": significance, "per_category": dict(per_category),
            "n_scored": len(methods["SentinelPR"]), "selected_family": selected,
            "family_pr_auc": {f: round(pr_auc(p), 4) for f, p in candidates.items()}}


# --- RQ1: change impact -----------------------------------------------------------------

def impact_evaluation(rows: list[dict]) -> dict:
    result = {}
    for method in ("lexical", "static", "sentinel", "sentinel_verified"):
        for level in ("tests", "modules"):
            relevant = [r["impact"][method][level] for r in rows if r["impact"][method][level]["actual"] > 0]
            if not relevant:
                continue
            tp = sum(x["tp"] for x in relevant)
            predicted = sum(x["predicted"] for x in relevant)
            actual = sum(x["actual"] for x in relevant)
            micro_p = tp / predicted if predicted else 0.0
            micro_r = tp / actual if actual else 0.0
            result.setdefault(method, {})[level] = {
                "prs": len(relevant),
                "macro_precision": round(float(np.mean([x["precision"] for x in relevant])), 4),
                "macro_recall": round(float(np.mean([x["recall"] for x in relevant])), 4),
                "macro_f1": round(float(np.mean([x["f1"] for x in relevant])), 4),
                "micro_precision": round(micro_p, 4), "micro_recall": round(micro_r, 4),
                "micro_f1": round(2 * micro_p * micro_r / (micro_p + micro_r), 4) if micro_p + micro_r else 0.0,
            }
    f1 = {m: [r["impact"][m]["tests"]["f1"] for r in rows if r["impact"]["sentinel"]["tests"]["actual"] > 0] for m in ("sentinel", "static", "lexical")}
    result["wilcoxon_tests_f1"] = {"sentinel_vs_static": wilcoxon(f1["sentinel"], f1["static"]), "sentinel_vs_lexical": wilcoxon(f1["sentinel"], f1["lexical"])}
    safe = [r["sentinel"]["safe_selection"] for r in rows if r["sentinel"]["safe_selection"] is not None]
    saved = [r["sentinel"]["time_saved"] for r in rows if not r["sentinel"]["rejected"]]
    result["selection"] = {
        "safe_selection_rate": round(float(np.mean(safe)), 4) if safe else None, "prs_with_failures": len(safe),
        "mean_time_saved": round(float(np.mean(saved)), 4) if saved else None,
        "mean_selected": round(float(np.mean([r["sentinel"]["selected"] for r in rows if not r["sentinel"]["rejected"]])), 2),
    }
    return result


# --- RQ2: claim verification -------------------------------------------------------------

def claim_evaluation(rows: list[dict]) -> dict:
    total = defaultdict(int)
    for r in rows:
        for k, v in r["claims"].items():
            total[k] += v
    before = total["true_before"] / total["checkable"] if total["checkable"] else None
    after = total["true_after"] / total["verified"] if total["verified"] else None
    history = defaultdict(int)
    for r in rows:
        for k, v in r.get("history_claims", {}).items():
            history[k] += v
    return {
        **dict(total),
        "precision_before_verification": round(before, 4) if before is not None else None,
        "precision_after_verification": round(after, 4) if after is not None else None,
        "false_claim_rate_before": round(1 - before, 4) if before is not None else None,
        "false_claim_rate_after": round(1 - after, 4) if after is not None else None,
        "recall_cost": round(1 - total["true_after"] / total["true_before"], 4) if total["true_before"] else None,
        "history_claims": dict(history),
    }


# --- RQ6: robustness ----------------------------------------------------------------------

def robustness_evaluation(rows: list[dict]) -> dict:
    attacks = [r for r in rows if r.get("attack")]
    benign = [r for r in rows if not r.get("attack")]
    by_attack: dict[str, dict] = defaultdict(lambda: {"n": 0, "passed": 0, "detected": 0})
    for r in attacks:
        entry = by_attack[r["attack"]]
        entry["n"] += 1
        entry["passed"] += r["sentinel"]["decision"] == "PASS"
        entry["detected"] += bool(set(r["sentinel"]["guards"]) & {"injection", "secret", "size"})
    result = {
        "attack_prs": len(attacks),
        "attack_success_rate": round(sum(r["sentinel"]["decision"] == "PASS" for r in attacks) / len(attacks), 4) if attacks else None,
        "by_attack": dict(by_attack),
        "guard_false_positive_rate": round(sum(bool(set(r["sentinel"]["guards"]) & {"injection", "secret"}) for r in benign) / len(benign), 4) if benign else None,
    }
    llm_rows = [r for r in attacks if "approve" in r.get("llm_reviewer", {})]
    if llm_rows:
        result["llm_only_gate_attack_success_rate"] = round(sum(r["llm_reviewer"]["approve"] for r in llm_rows) / len(llm_rows), 4)
    return result


# --- RQ5: efficiency ------------------------------------------------------------------------

def efficiency_evaluation(rows: list[dict]) -> dict:
    latency = [r["sentinel"]["elapsed_s"] for r in rows]
    tokens = [r["sentinel"]["tokens"] for r in rows]
    nodes: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for t in r["sentinel"]["trace"]:
            nodes[t["node"]].append(t["s"])
    return {
        "latency_s": {"p50": round(float(np.percentile(latency, 50)), 2), "p95": round(float(np.percentile(latency, 95)), 2), "mean": round(float(np.mean(latency)), 2)},
        "tokens_per_pr": {"mean": round(float(np.mean(tokens)), 1), "max": int(max(tokens))},
        "llm_calls_per_pr": round(float(np.mean([r["sentinel"]["llm_calls"] for r in rows])), 2),
        "node_mean_s": {k: round(float(np.mean(v)), 3) for k, v in sorted(nodes.items(), key=lambda kv: -np.mean(kv[1]))},
    }


def dataset_summary(rows: list[dict]) -> dict:
    by_category: dict[str, dict] = defaultdict(lambda: {"n": 0, "defective": 0, "tests_pass": 0})
    for r in rows:
        entry = by_category[r["category"]]
        entry["n"] += 1
        entry["defective"] += r["label"]
        entry["tests_pass"] += r["tests_pass"]
    return {"prs": len(rows), "defective": sum(r["label"] for r in rows), "bases": sorted({r["base"] for r in rows}, key=str),
            "by_category": dict(sorted(by_category.items()))}
