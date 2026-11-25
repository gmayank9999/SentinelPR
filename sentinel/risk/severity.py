"""Descriptive bands for the existing decision. Never makes a gate decision."""

from decimal import Decimal, InvalidOperation

SEVERITIES = ("LOW", "MEDIUM", "HIGH")
ACTIONS = {
    "PASS": "Proceed through normal review and release.",
    "CANARY": "Review the evidence and use the configured canary process when releasing.",
    "BLOCK": "Resolve the blocking evidence and rerun the analysis before merging.",
}


def verdict_fields(report: dict) -> dict:
    decision = report.get("decision")
    risk = report.get("risk", report.get("risk_score"))
    reason = report.get("decision_reason") or ""
    tests = (report.get("verification") or {}).get("tests") or {}
    existing = set(tests.get("preexisting_failures") or [])
    failed = any(isinstance(item, dict) and item.get("nodeid") not in existing for item in tests.get("failed") or [])
    override = report.get("policy_override")
    if decision == "BLOCK" and not override:
        if report.get("rejected"):
            override = {"rule": "input_guard", "reason": reason}
        elif failed or reason.endswith("test(s) fail on the head revision"):
            override = {"rule": "verified_test_failures", "reason": reason}
    severity = "HIGH" if override else report.get("severity")
    if not severity and risk is not None:
        thresholds = report.get("thresholds") or {}
        try:
            p = Decimal(str(risk))
            canary, block = (Decimal(str(thresholds[key])) for key in ("canary", "block"))
            intervals = {"PASS": (Decimal(0), canary), "CANARY": (canary, block), "BLOCK": (block, Decimal(1))}
            lower, upper = intervals[decision]
            in_interval = lower <= p and (p < upper or (decision == "BLOCK" and p == upper))
            if in_interval and Decimal(0) <= canary <= block <= Decimal(1):
                # Compute the exact rational boundaries, then use their nearest
                # representable float because that is how scores are stored.
                boundaries = [float((lower * (3 - i) + upper * i) / 3) for i in (1, 2)]
                severity = SEVERITIES[sum(float(p) >= cutoff for cutoff in boundaries)]
        except (InvalidOperation, KeyError, TypeError, ValueError):
            pass
    return {
        "severity": severity,
        "detailed_decision": f"{decision}_{severity}" if severity else None,
        "risk_score": risk,
        "policy_override": override,
        "recommended_action": ACTIONS.get(decision),
    }


def with_verdict(report: dict) -> dict:
    return report | verdict_fields(report)
