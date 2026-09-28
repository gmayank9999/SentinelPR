"""Per-PR evaluation helpers shared by the runner and the analysis."""

from __future__ import annotations


def function_level(test_id: str) -> str:
    """``tests/x.py::test_y[3-A]`` -> ``tests/x.py::test_y``."""
    return test_id.split("[", 1)[0]


def prf(predicted: set[str], actual: set[str]) -> dict:
    tp = len(predicted & actual)
    precision = tp / len(predicted) if predicted else (1.0 if not actual else 0.0)
    recall = tp / len(actual) if actual else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "tp": tp, "predicted": len(predicted), "actual": len(actual)}


def impact_scores(claims: list[dict], gt_tests: list[str], gt_modules: list[str], verified_only: bool = False) -> dict:
    keep = [c for c in claims if not verified_only or c.get("status") == "VERIFIED"]
    tests = {function_level(c["target"]) for c in keep if c["type"] == "test_impact"}
    modules = {c["target"] for c in keep if c["type"] == "module_impact"}
    return {
        "tests": prf(tests, {function_level(t) for t in gt_tests}),
        "modules": prf(modules, set(gt_modules)),
    }


def claim_accuracy(claims: list[dict], gt_tests: list[str], gt_modules: list[str]) -> dict:
    """RQ2: how many agent claims are actually true, before and after verification."""
    truth_tests = {function_level(t) for t in gt_tests}
    truth_modules = set(gt_modules)

    def true(c: dict) -> bool | None:
        if c["type"] == "test_impact":
            return function_level(c["target"]) in truth_tests
        if c["type"] == "module_impact":
            return c["target"] in truth_modules
        return None  # no execution ground truth for history/rationale/api claims

    checkable = [c for c in claims if true(c) is not None]
    verified = [c for c in checkable if c.get("status") == "VERIFIED"]
    return {
        "claims": len(claims),
        "checkable": len(checkable),
        "true_before": sum(1 for c in checkable if true(c)),
        "verified": len(verified),
        "true_after": sum(1 for c in verified if true(c)),
        "refuted": sum(1 for c in claims if c.get("status") == "REFUTED"),
        "unverifiable": sum(1 for c in claims if c.get("status") == "UNVERIFIABLE"),
    }
