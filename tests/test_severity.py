import math
from decimal import Decimal

import pytest

from sentinel.publish.render import check_run_output, render_comment
from sentinel.risk.policy import decide
from sentinel.risk.severity import with_verdict

THRESHOLDS = {"canary": 0.4982, "block": 0.8672}


def classify(risk, failures=0, thresholds=THRESHOLDS):
    policy = decide(risk, failures, thresholds)
    return with_verdict({"decision": policy.decision, "decision_reason": policy.reason, "thresholds": thresholds, "risk": risk})


@pytest.mark.parametrize(
    ("risk", "detail"),
    [(0, "PASS_LOW"), (0.2, "PASS_MEDIUM"), (0.4, "PASS_HIGH"),
     (0.4982, "CANARY_LOW"), (0.64, "CANARY_MEDIUM"), (0.8, "CANARY_HIGH"),
     (0.8672, "BLOCK_LOW"), (0.94, "BLOCK_MEDIUM"), (1, "BLOCK_HIGH")],
)
def test_nine_bands(risk, detail):
    r = classify(risk)
    assert r["detailed_decision"] == detail
    assert r["decision"] == detail.split("_")[0]
    assert r["risk_score"] == r["risk"] == risk
    assert r["policy_override"] is None


@pytest.mark.parametrize("lower,upper,decision", [("0", ".4982", "PASS"), (".4982", ".8672", "CANARY"), (".8672", "1", "BLOCK")])
def test_internal_cutoffs_include_lower_edge(lower, upper, decision):
    lo, hi = Decimal(lower), Decimal(upper)
    first, second = (float((lo * (3 - i) + hi * i) / 3) for i in (1, 2))
    for cutoff, below, at in [(first, "LOW", "MEDIUM"), (second, "MEDIUM", "HIGH")]:
        assert classify(math.nextafter(cutoff, 0))["detailed_decision"] == f"{decision}_{below}"
        assert classify(cutoff)["detailed_decision"] == f"{decision}_{at}"


def test_primary_cutoffs_and_custom_config():
    assert classify(math.nextafter(0.4982, 0))["detailed_decision"] == "PASS_HIGH"
    assert classify(math.nextafter(0.8672, 0))["detailed_decision"] == "CANARY_HIGH"
    assert classify(0.2, thresholds={"canary": 0.3, "block": 0.6})["detailed_decision"] == "PASS_HIGH"


def test_hard_blocks_keep_primary_decisions_and_reason_without_fabricating_risk():
    r = classify(0.01, failures=2)
    assert r["decision"] == "BLOCK" and r["detailed_decision"] == "BLOCK_HIGH"
    assert r["policy_override"] == {"rule": "verified_test_failures", "reason": "2 test(s) fail on the head revision"}
    r = with_verdict({"decision": "BLOCK", "risk": None, "rejected": True, "decision_reason": "Secret detected"})
    assert r["risk_score"] is None and r["detailed_decision"] == "BLOCK_HIGH"
    assert r["policy_override"]["reason"] == "Secret detected"
    assert with_verdict({"decision": "PASS", "risk": None})["detailed_decision"] is None


def test_legacy_reports_render_without_changes_to_the_source_or_gate():
    old = {"run_id": "run-20261124000000-abcdef", "decision": "BLOCK", "risk": 0.89,
           "decision_reason": "risk exceeds threshold", "thresholds": THRESHOLDS}
    assert "BLOCK_LOW" in render_comment(old, "")
    assert check_run_output(old)["title"].startswith("BLOCK_LOW")
    assert "BLOCK_LOW" in check_run_output(old)["summary"]
    assert "detailed_decision" not in old
    assert "risk unavailable" in render_comment(old | {"risk": None}, "")
