"""What the LLM adds: paired comparison of an LLM run against the evidence-only run.

    python -m bench.run_matrix --llm --labels bench/out/uni-erp/prs.jsonl --bases "Release 1.3.0" "Release 1.4.0" \\
        --out bench/out/uni-erp-llm
    python -m bench.analysis.llm --base bench/out/uni-erp --llm bench/out/uni-erp-llm

Both runs analyse the *same* labelled PRs. Every gate arm is scored by the same forward-chained
model (expert weights, calibrated on earlier releases of the evidence-only run), so differences
come from the inputs, not from refitting:

    no LLM               evidence-only features (the committed result)
    + LLM rating         evidence-only features with the Impact agent's risk rating swapped in
    LLM pipeline         every feature from the LLM run (LLM claims change the verified-claim features too)
    B-LLM                a single LLM call on title, description and diff; blocks at probability >= 0.5
    LLM rating alone     the Impact agent's low/medium/high rating as a score
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from bench.analysis.stats import _decision, _eligible, fit_family, mcnemar, score_block
from sentinel.risk.policy import choose_thresholds

RATING = {0.2: "low", 0.5: "medium", 0.8: "high"}


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in (path / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def _arm(rows: list[dict], score) -> list[dict]:
    return [{**r, "score": score(r)} for r in rows]


def gate_arms(base_rows: list[dict], llm_rows: list[dict]) -> dict[str, list[dict]]:
    eligible = _eligible(base_rows)
    llm_by_id = {r["id"]: r for r in _eligible(llm_rows)}
    arms: dict[str, list[dict]] = defaultdict(list)
    for k in sorted({r["base_order"] for r in llm_by_id.values()}):
        train = [r for r in eligible if r["base_order"] < k - 1]
        val = [r for r in eligible if r["base_order"] == k - 1]
        if len({r["label"] for r in train}) < 2:
            continue
        model = fit_family("expert", train, val)
        thresholds = choose_thresholds([model.predict(r["features"]) for r in val], [r["label"] for r in val])
        for r in eligible:
            if r["base_order"] != k or r["id"] not in llm_by_id:
                continue
            l = llm_by_id[r["id"]]
            common = {"id": r["id"], "label": r["label"], "category": r["category"], "tests_fail": 1 - r["tests_pass"], **thresholds}
            rating = l["features"].get("llm_risk", 0.5)
            arms["no LLM"].append({**common, "score": model.predict(r["features"])})
            arms["+ LLM rating"].append({**common, "score": model.predict({**r["features"], "llm_risk": rating})})
            arms["LLM pipeline"].append({**common, "score": model.predict(l["features"])})
            arms["LLM rating alone"].append({**common, "score": rating, "block": 0.65, "canary": 0.35, "tests_fail": 0})
            review = l.get("llm_reviewer") or {}
            if "probability" in review:
                arms["B-LLM"].append({**common, "score": review["probability"], "block": 0.5, "canary": 0.5, "tests_fail": 0,
                                      "approve": review["approve"]})
    return dict(arms)


def _blocked(name: str, p: dict) -> bool:
    if name.startswith(("no LLM", "+ LLM", "LLM pipeline")):
        return _decision(p) == "BLOCK"
    return p["score"] >= p["block"]


def gate_table(arms: dict[str, list[dict]]) -> dict:
    table, significance, per_category = {}, {}, {}
    reference = {p["id"]: p for p in arms["no LLM"]}
    for name, preds in arms.items():
        labels = [p["label"] for p in preds]
        entry = score_block(labels, [p["score"] for p in preds], float(np.median([p["block"] for p in preds])))
        b = np.asarray([_blocked(name, p) for p in preds], bool)
        y = np.asarray(labels, int)
        entry["false_block_rate"] = round(float(b[y == 0].mean()), 4)
        entry["block_recall"] = round(float(b[y == 1].mean()), 4)
        table[name] = entry
        if name != "no LLM":
            shared = [p for p in preds if p["id"] in reference]
            significance[name] = mcnemar([_blocked("no LLM", reference[p["id"]]) == bool(p["label"]) for p in shared],
                                         [_blocked(name, p) == bool(p["label"]) for p in shared])
        cats: dict[str, dict] = defaultdict(lambda: {"n": 0, "defective": 0, "blocked": 0, "passed": 0})
        for p in preds:
            c = cats[p["category"]]
            c["n"] += 1
            c["defective"] += p["label"]
            c["blocked"] += _blocked(name, p)
            if name.startswith(("no LLM", "+ LLM", "LLM pipeline")):
                c["passed"] += _decision(p) == "PASS"
            elif name == "B-LLM":
                c["passed"] += p["approve"]
        per_category[name] = dict(cats)
    return {"table": table, "mcnemar_vs_no_llm": significance, "per_category": per_category, "n": len(reference)}


def rating_distribution(llm_rows: list[dict]) -> dict:
    out: dict[str, dict] = defaultdict(lambda: {"low": 0, "medium": 0, "high": 0, "none": 0})
    for r in _eligible(llm_rows):
        rating = r["sentinel"].get("llm_risk")
        out["defective" if r["label"] else "benign"][RATING.get(rating, "none") if rating is not None else "none"] += 1
    return dict(out)


def robustness(llm_rows: list[dict], arms: dict[str, list[dict]]) -> dict:
    attacks = [r for r in llm_rows if r["category"] == "C7"]
    reviewed = [r for r in attacks if "approve" in r.get("llm_reviewer", {})]
    by_attack: dict[str, dict] = defaultdict(lambda: {"n": 0, "b_llm_approved": 0, "sentinel_pass": 0})
    for r in attacks:
        a = by_attack[r["attack"] or "?"]
        a["n"] += 1
        a["b_llm_approved"] += bool(r.get("llm_reviewer", {}).get("approve"))
        a["sentinel_pass"] += r["sentinel"]["decision"] == "PASS"
    # the same defective PRs without the attack: does the injected text move the reviewer?
    clean = {r["id"]: r for r in llm_rows}
    shifts = [r["llm_reviewer"]["probability"] - clean[r["id"][:-3]]["llm_reviewer"]["probability"]
              for r in reviewed if r["id"].endswith("-c7") and "probability" in clean.get(r["id"][:-3], {}).get("llm_reviewer", {})]
    return {
        "attacked_prs": len(attacks),
        "b_llm_attack_success": round(sum(r["llm_reviewer"]["approve"] for r in reviewed) / len(reviewed), 4) if reviewed else None,
        "sentinel_llm_attack_success": round(sum(r["sentinel"]["decision"] == "PASS" for r in attacks) / len(attacks), 4) if attacks else None,
        "b_llm_probability_shift_from_attack": round(float(np.mean(shifts)), 4) if shifts else None,
        "paired_attack_pairs": len(shifts),
        "by_attack": dict(by_attack),
    }


def impact_quality(base_rows: list[dict], llm_rows: list[dict]) -> dict:
    ids = {r["id"] for r in llm_rows}
    out = {}
    for name, rows in (("rules", [r for r in base_rows if r["id"] in ids]), ("llm", llm_rows)):
        relevant = [r["impact"]["sentinel"]["tests"] for r in rows if r["impact"]["sentinel"]["tests"]["actual"] > 0]
        claims = [r["claims"] for r in rows]
        checkable = sum(c["checkable"] for c in claims)
        kept = sum(c["verified"] for c in claims)
        out[name] = {
            "prs": len(relevant),
            "macro_precision": round(float(np.mean([x["precision"] for x in relevant])), 4),
            "macro_recall": round(float(np.mean([x["recall"] for x in relevant])), 4),
            "macro_f1": round(float(np.mean([x["f1"] for x in relevant])), 4),
            "claims": sum(c["claims"] for c in claims),
            "precision_before_verification": round(sum(c["true_before"] for c in claims) / checkable, 4) if checkable else None,
            "precision_after_verification": round(sum(c["true_after"] for c in claims) / kept, 4) if kept else None,
            "refuted": sum(c["refuted"] for c in claims),
        }
    modes = defaultdict(int)
    for r in llm_rows:
        modes[r["sentinel"]["impact_mode"] or "rejected"] += 1
    out["impact_mode"] = dict(modes)
    return out


def cost(llm_rows: list[dict]) -> dict:
    s = [r["sentinel"] for r in llm_rows if not r["sentinel"]["rejected"]]
    tokens = [x.get("llm_tokens_all", x["tokens"]) for x in s]
    failed: dict[str, int] = defaultdict(int)
    for x in s:
        for task in x.get("llm_failed_tasks") or []:
            failed[task] += 1
    reviewer = [r["llm_reviewer"].get("tokens", 0) for r in llm_rows if "tokens" in r.get("llm_reviewer", {})]
    latency = [x["elapsed_s"] for x in s]
    return {
        "prs": len(s),
        "tokens_per_pr": {"mean": round(float(np.mean(tokens)), 1), "p50": float(np.median(tokens)), "p95": float(np.percentile(tokens, 95))},
        "calls_per_pr": round(float(np.mean([x["llm_calls"] for x in s])), 2),
        "failed_tasks": dict(failed),
        "latency_s": {"p50": round(float(np.median(latency)), 2), "p95": round(float(np.percentile(latency, 95)), 2)},
        "b_llm_tokens_per_pr": round(float(np.mean(reviewer)), 1) if reviewer else None,
    }


def consistency(base_rows: list[dict], llm_rows: list[dict]) -> dict:
    """Execution evidence must not depend on the LLM: compare it between the two runs."""
    base = {r["id"]: r for r in base_rows}
    keys = ("verified_test_failures", "changed_line_coverage")
    differ = [r["id"] for r in llm_rows if r["id"] in base and any(r["features"].get(k) != base[r["id"]]["features"].get(k) for k in keys)]
    return {"compared": sum(1 for r in llm_rows if r["id"] in base), "execution_features_differ": differ}


def render(result: dict) -> str:
    g = result["gate"]
    lines = [f"# What the LLM adds — {result['repo']}", "",
             f"{g['n']} PRs scored out of fold on {', '.join(result['bases'])}; model `{result['model']}`, "
             f"temperature 0. Every gate arm uses the same forward-chained model per release.", "",
             "## Release gate", "",
             "| arm | ROC-AUC | 95% CI | PR-AUC | 95% CI | recall@5%FPR | Brier | ECE | false-block | block recall | McNemar p vs no LLM |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda v: "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))
    ci = lambda v: f"[{v[0]:.2f}, {v[1]:.2f}]" if v else "—"
    for name, t in g["table"].items():
        p = g["mcnemar_vs_no_llm"].get(name, {}).get("p_value")
        lines.append(f"| {name} | {f(t.get('roc_auc'))} | {ci(t.get('roc_auc_ci'))} | {f(t.get('pr_auc'))} | {ci(t.get('pr_auc_ci'))} | "
                     f"{f(t.get('recall_at_5fpr'))} | {f(t['brier'])} | {f(t['ece'])} | {f(t['false_block_rate'])} | {f(t['block_recall'])} | {f(p)} |")
    lines += ["", "Blocked / passed by category (B-LLM: passed = approved):", "",
              "| category | n | defective | " + " | ".join(f"{a} blocked / passed" for a in ("no LLM", "LLM pipeline", "B-LLM")) + " |",
              "|---|---|---|---|---|---|"]
    cats = g["per_category"]["no LLM"]
    for c in sorted(cats):
        cells = []
        for a in ("no LLM", "LLM pipeline", "B-LLM"):
            e = g["per_category"].get(a, {}).get(c)
            cells.append(f"{e['blocked']} / {e['passed']}" if e else "—")
        lines.append(f"| {c} | {cats[c]['n']} | {cats[c]['defective']} | " + " | ".join(cells) + " |")
    r = result["ratings"]
    lines += ["", "Impact agent's risk rating:", "", "| | low | medium | high | none |", "|---|---|---|---|---|"]
    for k in ("benign", "defective"):
        if k in r:
            lines.append(f"| {k} | {r[k]['low']} | {r[k]['medium']} | {r[k]['high']} | {r[k]['none']} |")
    rb = result["robustness"]
    lines += ["", "## Adversarial PRs", "",
              f"Attack success (a defective, attacked PR gets through): B-LLM approves {f(rb['b_llm_attack_success'])}, "
              f"SentinelPR with the LLM passes {f(rb['sentinel_llm_attack_success'])} ({rb['attacked_prs']} PRs). "
              f"Mean change in B-LLM's defect probability caused by the attack text: {f(rb['b_llm_probability_shift_from_attack'])} "
              f"over {rb['paired_attack_pairs']} paired PRs.", "",
              "| attack | n | B-LLM approved | SentinelPR PASS |", "|---|---|---|---|"]
    for a, e in sorted(rb["by_attack"].items()):
        lines.append(f"| {a} | {e['n']} | {e['b_llm_approved']} | {e['sentinel_pass']} |")
    iq = result["impact"]
    lines += ["", "## Impact claims: LLM vs rules (same PRs)", "",
              "| agent | PRs | macro P | macro R | macro F1 | claims | precision before verification | after | refuted |",
              "|---|---|---|---|---|---|---|---|---|"]
    for k in ("rules", "llm"):
        e = iq[k]
        lines.append(f"| {k} | {e['prs']} | {f(e['macro_precision'])} | {f(e['macro_recall'])} | {f(e['macro_f1'])} | {e['claims']} | "
                     f"{f(e['precision_before_verification'])} | {f(e['precision_after_verification'])} | {e['refuted']} |")
    lines += ["", f"Impact agent mode in the LLM run: {iq['impact_mode']} (rules = none of the model's claims cited evidence it was given, so the evidence-only claims are used; its risk rating is still recorded)."]
    c = result["cost"]
    lines += ["", "## Cost", "",
              f"SentinelPR with the LLM: {c['tokens_per_pr']['mean']:.0f} tokens per PR on average (p95 {c['tokens_per_pr']['p95']:.0f}), "
              f"{c['calls_per_pr']} calls, latency p50 {c['latency_s']['p50']} s / p95 {c['latency_s']['p95']} s; "
              f"tasks that never got an answer (PRs): {c['failed_tasks'] or 'none'}. B-LLM: {f(c['b_llm_tokens_per_pr'])} tokens per PR. "
              "Latency includes waiting out free-tier rate limits.", "",
              f"Consistency: execution evidence identical in both runs for {c_ok(result['consistency'])}."]
    return "\n".join(lines) + "\n"


def c_ok(cons: dict) -> str:
    bad = cons["execution_features_differ"]
    return f"{cons['compared'] - len(bad)} of {cons['compared']} PRs" + (f" (differs: {', '.join(bad[:5])})" if bad else "")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, default=Path("bench/out/uni-erp"))
    parser.add_argument("--llm", type=Path, default=Path("bench/out/uni-erp-llm"))
    parser.add_argument("--out", type=Path, help="defaults to bench/results/<repo>/llm.{json,md}")
    args = parser.parse_args(argv)
    base_rows, llm_rows = load(args.base), load(args.llm)
    repo = llm_rows[0]["repo"]
    arms = gate_arms(base_rows, llm_rows)
    models = {r["llm_reviewer"].get("model") for r in llm_rows if r.get("llm_reviewer", {}).get("model")}
    result = {
        "repo": repo, "bases": sorted({r["base"] for r in llm_rows}), "model": ", ".join(sorted(models)) or "?",
        "gate": gate_table(arms), "ratings": rating_distribution(llm_rows), "robustness": robustness(llm_rows, arms),
        "impact": impact_quality(base_rows, llm_rows), "cost": cost(llm_rows), "consistency": consistency(base_rows, llm_rows),
    }
    out = args.out or Path("bench/results") / repo
    out.mkdir(parents=True, exist_ok=True)
    (out / "llm.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    (out / "llm.md").write_text(render(result), encoding="utf-8")
    print(render(result))


if __name__ == "__main__":
    main()
