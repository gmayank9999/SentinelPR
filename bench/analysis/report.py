"""Turn benchmark rows into results, a Markdown report and the deployable risk model.

    python -m bench.analysis.report --results bench/out/uni-erp/results.jsonl

Writes, next to the input:
    results.json   every table and curve (the dashboard's Evaluation Lab reads this)
    report.md      the same numbers as a readable report
and trains the production model (all bases but the latest for fitting, the latest for
calibration and thresholds) into ``sentinel/risk/artifacts/model.json``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from bench.analysis.stats import (
    claim_evaluation,
    dataset_summary,
    efficiency_evaluation,
    gate_evaluation,
    impact_evaluation,
    robustness_evaluation,
)
from sentinel.risk.features import GROUPS
from sentinel.risk.model import train
from sentinel.risk.policy import choose_thresholds

ROOT = Path(__file__).resolve().parents[2]


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def train_production_model(rows: list[dict], out: Path) -> dict:
    scored = [r for r in rows if not r["sentinel"]["rejected"] and r["sentinel"]["risk"] is not None]
    latest = max(r["base_order"] for r in scored)
    fit_rows = [r for r in scored if r["base_order"] < latest]
    cal_rows = [r for r in scored if r["base_order"] == latest]
    model = train([r["features"] for r in fit_rows], [r["label"] for r in fit_rows], feature_names=GROUPS["full"],
                  validation=([r["features"] for r in cal_rows], [r["label"] for r in cal_rows]), name="uni-erp-benchmark")
    model.thresholds = choose_thresholds([model.predict(r["features"]) for r in cal_rows], [r["label"] for r in cal_rows])
    model.metadata.update({
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fit_bases": sorted({r["base"] for r in fit_rows}), "calibration_base": cal_rows[0]["base"] if cal_rows else None,
        # thresholds from the evaluation are what the policy uses
        "evaluation": {"thresholds": model.thresholds, "calibration_size": len(cal_rows)},
    })
    model.save(out)
    top = sorted(zip(model.feature_names, model.coef), key=lambda kv: -abs(kv[1]))[:10]
    return {"path": str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out), "thresholds": model.thresholds,
            "top_coefficients": [[n, round(c, 3)] for n, c in top], **{k: model.metadata[k] for k in ("fit_bases", "calibration_base")}}


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return f"[{v[0]:.2f}, {v[1]:.2f}]"
    return str(v)


def markdown(results: dict) -> str:
    d, gate, impact, claims, robust, eff = (results[k] for k in ("dataset", "gate", "impact", "claims", "robustness", "efficiency"))
    lines = [f"# SentinelPR benchmark — {results['repo']}", "", f"_Generated {results['generated_at']}._", "",
             "## Dataset", "", f"{d['prs']} pull requests over {len(d['bases'])} base revisions ({', '.join(d['bases'])}); "
             f"{d['defective']} defective by execution.", "", "| category | PRs | defective | visible tests pass |", "|---|---|---|---|"]
    for cat, e in d["by_category"].items():
        lines.append(f"| {cat} | {e['n']} | {e['defective']} | {e['tests_pass']} |")

    lines += ["", "## RQ3 — release-risk gate (forward-chained, out-of-fold)", "",
              f"{gate['n_scored']} PRs scored by models that never saw a later revision.", "",
              "| method | ROC-AUC | 95% CI | PR-AUC | 95% CI | recall@5%FPR | Brier | ECE | false-block | block recall | McNemar p |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in gate["table"].items():
        p = gate["mcnemar_vs_sentinel"].get(name, {}).get("p_value")
        lines.append(f"| {name} | {_fmt(m.get('roc_auc'))} | {_fmt(m.get('roc_auc_ci'))} | {_fmt(m.get('pr_auc'))} | {_fmt(m.get('pr_auc_ci'))} | "
                     f"{_fmt(m.get('recall_at_5fpr'))} | {_fmt(m.get('brier'))} | {_fmt(m.get('ece'))} | {_fmt(m.get('false_block_rate'))} | "
                     f"{_fmt(m.get('block_recall'))} | {_fmt(p)} |")
    lines += ["", "Decisions by category (SentinelPR):", "", "| category | n | defective | PASS | CANARY | BLOCK |", "|---|---|---|---|---|---|"]
    for cat, e in sorted(gate["per_category"].items()):
        lines.append(f"| {cat} | {e['n']} | {e['defective']} | {e['PASS']} | {e['CANARY']} | {e['BLOCK']} |")

    lines += ["", "## RQ1 — change impact (tests)", "", "| method | PRs | macro P | macro R | macro F1 | micro F1 |", "|---|---|---|---|---|---|"]
    for method in ("lexical", "static", "sentinel", "sentinel_verified"):
        m = impact.get(method, {}).get("tests")
        if m:
            lines.append(f"| {method} | {m['prs']} | {m['macro_precision']:.3f} | {m['macro_recall']:.3f} | {m['macro_f1']:.3f} | {m['micro_f1']:.3f} |")
    sel = impact["selection"]
    lines += ["", f"Test selection: {sel['mean_selected']} tests per PR on average, {_fmt(sel['mean_time_saved'])} of suite time saved, "
              f"safe-selection rate {_fmt(sel['safe_selection_rate'])} over {sel['prs_with_failures']} PRs with failing tests. "
              f"Wilcoxon (test F1): vs static p={_fmt(impact['wilcoxon_tests_f1']['sentinel_vs_static'])}, "
              f"vs lexical p={_fmt(impact['wilcoxon_tests_f1']['sentinel_vs_lexical'])}."]

    lines += ["", "## RQ2 — claim verification", "",
              f"{claims.get('checkable', 0)} execution-checkable impact claims. Precision before verification "
              f"{_fmt(claims['precision_before_verification'])}, after {_fmt(claims['precision_after_verification'])} "
              f"(false-claim rate {_fmt(claims['false_claim_rate_before'])} → {_fmt(claims['false_claim_rate_after'])}; "
              f"true claims lost: {_fmt(claims['recall_cost'])}). Refuted: {claims.get('refuted', 0)}, unverifiable: {claims.get('unverifiable', 0)}. "
              f"History claims: {claims['history_claims']}."]

    lines += ["", "## RQ6 — adversarial robustness", "",
              f"Attack success rate (defective + attack PR receives PASS): {_fmt(robust['attack_success_rate'])} over {robust['attack_prs']} PRs. "
              f"Guardrail false-positive rate on non-attack PRs: {_fmt(robust['guard_false_positive_rate'])}."]
    if "llm_only_gate_attack_success_rate" in robust:
        lines.append(f"LLM-only gate (B-LLM approves): {_fmt(robust['llm_only_gate_attack_success_rate'])}.")
    lines += ["", "| attack | n | received PASS | detected by guards |", "|---|---|---|---|"]
    for name, e in robust["by_attack"].items():
        lines.append(f"| {name} | {e['n']} | {e['passed']} | {e['detected']} |")

    lines += ["", "## RQ5 — cost and latency", "",
              f"Latency per PR: p50 {eff['latency_s']['p50']}s, p95 {eff['latency_s']['p95']}s. LLM tokens per PR: mean {eff['tokens_per_pr']['mean']}. "
              f"Slowest stages: " + ", ".join(f"{k} {v}s" for k, v in list(eff["node_mean_s"].items())[:4]) + "."]
    model = results.get("model")
    if model:
        lines += ["", "## Deployed model", "", f"Trained on {', '.join(model['fit_bases'])}; calibrated and thresholded on {model['calibration_base']}: "
                  f"canary ≥ {model['thresholds']['canary']}, block ≥ {model['thresholds']['block']}.", "",
                  "Largest standardised coefficients: " + ", ".join(f"`{n}` {c:+.2f}" for n, c in model["top_coefficients"]) + "."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, default=ROOT / "bench" / "out" / "uni-erp" / "results.jsonl")
    parser.add_argument("--model-out", type=Path, default=ROOT / "sentinel" / "risk" / "artifacts" / "model.json")
    parser.add_argument("--no-model", action="store_true")
    args = parser.parse_args(argv)
    rows = load(args.results)
    results = {
        "repo": rows[0]["repo"] if rows else "?",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": dataset_summary(rows),
        "gate": gate_evaluation(rows),
        "impact": impact_evaluation(rows),
        "claims": claim_evaluation(rows),
        "robustness": robustness_evaluation(rows),
        "efficiency": efficiency_evaluation(rows),
    }
    if not args.no_model:
        results["model"] = train_production_model(rows, args.model_out)
    out_dir = args.results.parent
    (out_dir / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    (out_dir / "report.md").write_text(markdown(results), encoding="utf-8")
    print(markdown(results))


if __name__ == "__main__":
    main()
