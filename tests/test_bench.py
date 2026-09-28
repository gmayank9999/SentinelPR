import random

from bench.analysis.report import markdown
from bench.analysis.stats import (
    claim_evaluation,
    dataset_summary,
    efficiency_evaluation,
    forward_chain,
    gate_evaluation,
    impact_evaluation,
    mcnemar,
    robustness_evaluation,
)
from bench.generate.adversarial import weaponise
from bench.generate.base import PRSpec
from bench.metrics import claim_accuracy, function_level, impact_scores, prf
from sentinel.risk.features import build_features


def fake_rows(n_per_base=40, bases=4, seed=3):
    rng = random.Random(seed)
    rows = []
    for b in range(bases):
        for i in range(n_per_base):
            label = int(rng.random() < 0.45)
            f = build_features({"log_churn": rng.uniform(0, 4)})
            f["surviving_mutants"] = rng.randint(1, 5) if label else rng.randint(0, 1)
            f["mutation_score"] = rng.uniform(0, 0.6) if label else rng.uniform(0.5, 1)
            attack = "injection-body" if label and i % 9 == 0 else None
            impact = {"tests": prf({"t::a"}, {"t::a", "t::b"}), "modules": prf(set(), set())}
            rows.append({
                "id": f"b{b}-{i}", "base": f"base{b}", "base_order": b, "category": "HF" if label else "C1", "label": label,
                "attack": attack, "tests_pass": int(not (label and i % 3 == 0)), "features": f,
                "sentinel": {"rejected": False, "risk": 0.5, "decision": "BLOCK" if label else "PASS", "guards": ["injection"] if attack else [],
                             "safe_selection": 1.0 if label else None, "time_saved": 0.6, "selected": 5, "elapsed_s": 8.0 + i % 3,
                             "tokens": 0, "llm_calls": 0, "trace": [{"node": "verify", "s": 5.0}]},
                "impact": {m: impact for m in ("lexical", "static", "sentinel", "sentinel_verified")},
                "claims": {"claims": 4, "checkable": 4, "true_before": 2, "verified": 2, "true_after": 2, "refuted": 2, "unverifiable": 0},
                "history_claims": {"VERIFIED": 1},
            })
    return rows


def test_prf_and_function_level():
    assert function_level("tests/a.py::test_x[1-2]") == "tests/a.py::test_x"
    assert prf({"a", "b"}, {"b", "c"})["f1"] == 0.5
    assert prf(set(), set())["precision"] == 1.0
    claims = [{"type": "test_impact", "target": "t.py::a", "status": "VERIFIED"}, {"type": "test_impact", "target": "t.py::z", "status": "REFUTED"}]
    assert impact_scores(claims, ["t.py::a[1]"], [])["tests"]["precision"] == 0.5
    assert impact_scores(claims, ["t.py::a[1]"], [], verified_only=True)["tests"]["precision"] == 1.0
    acc = claim_accuracy(claims, ["t.py::a"], [])
    assert (acc["true_before"], acc["verified"], acc["true_after"]) == (1, 1, 1)


def test_mcnemar():
    assert mcnemar([True] * 10, [True] * 10)["p_value"] == 1.0
    result = mcnemar([True] * 20, [False] * 20)
    assert result["a_only"] == 20 and result["p_value"] < 0.001


def test_forward_chain_never_uses_future_bases():
    rows = fake_rows()
    preds = forward_chain(rows)
    assert {p["id"].split("-")[0] for p in preds} == {"b2", "b3"}  # only bases with two predecessors are scored


def test_full_analysis_and_report():
    rows = fake_rows()
    gate = gate_evaluation(rows)
    assert gate["table"]["SentinelPR"]["roc_auc"] > gate["table"]["B-JIT"]["roc_auc"]
    assert "B-tests" in gate["table"] and "SentinelPR" not in gate["mcnemar_vs_sentinel"]
    results = {"repo": "fake", "generated_at": "now", "dataset": dataset_summary(rows), "gate": gate, "impact": impact_evaluation(rows),
               "claims": claim_evaluation(rows), "robustness": robustness_evaluation(rows), "efficiency": efficiency_evaluation(rows)}
    assert results["claims"]["precision_before_verification"] == 0.5 and results["claims"]["precision_after_verification"] == 1.0
    assert results["robustness"]["attack_success_rate"] == 0.0
    text = markdown(results)
    assert "RQ3" in text and "| SentinelPR |" in text


def test_weaponise_variants():
    spec = PRSpec("x", "r", "b", "HF", "Tweak", "Body", {"a.py": "def f():\n    return 1\n"}, "mutation:cmp", {"line": 2})
    assert "approve" in weaponise(spec, "injection-title", random.Random(0)).title
    commented = weaponise(spec, "injection-comment", random.Random(0)).edits["a.py"]
    assert commented.splitlines()[1].strip().startswith("# AI code reviewers")
    assert "SERVICE_TOKEN" in weaponise(spec, "fake-secret", random.Random(0)).edits["a.py"]
    big = weaponise(spec, "oversized", random.Random(0))
    assert big.edits["unierp/lookup_table.py"].count("\n") > 15000 and big.category == "C7"
