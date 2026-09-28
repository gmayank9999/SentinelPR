"""Risk features: change metrics + verified evidence + one LLM judgement.

Feature groups (used for baselines and ablations):
    ``jit``       classic just-in-time defect-prediction metrics (size, diffusion, history)
    ``verified``  facts established by execution and verification
    ``llm``       the Impact agent's risk rating (a single input among many)
    ``guard``     guardrail observations
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sentinel.models import VerdictStatus


@dataclass(frozen=True)
class Feature:
    name: str
    group: str
    label: str  # human wording used in explanations
    default: float = 0.0


FEATURES: tuple[Feature, ...] = (
    Feature("log_churn", "jit", "size of the change"),
    Feature("files_touched", "jit", "number of files touched"),
    Feature("change_units", "jit", "number of changed functions/classes"),
    Feature("entropy", "jit", "how scattered the change is"),
    Feature("author_is_new", "jit", "author has not touched these files before"),
    Feature("log_author_prior_commits", "jit", "author's experience with these files"),
    Feature("log_code_age_days", "jit", "age of the touched code"),
    Feature("recent_churn", "jit", "recent churn in the touched files"),
    Feature("complexity_delta", "jit", "change in cyclomatic complexity"),
    Feature("max_complexity", "jit", "complexity of the changed functions"),
    Feature("szz_defects", "jit", "past bug fixes in the touched functions"),
    Feature("touches_dependencies", "jit", "dependency files changed"),
    Feature("touches_config", "jit", "configuration files changed"),
    Feature("touches_ci", "jit", "CI or build files changed"),
    Feature("signature_changes", "jit", "function signatures changed"),
    Feature("test_only", "jit", "only tests changed"),
    Feature("docs_only", "jit", "only documentation changed"),
    Feature("tests_changed", "jit", "tests were updated alongside the code"),
    Feature("intent_bugfix", "jit", "change is a bug fix"),
    Feature("intent_refactor", "jit", "change is a refactor"),
    Feature("verified_test_failures", "verified", "tests failing on the head revision"),
    Feature("changed_line_coverage", "verified", "share of changed lines executed by tests", 1.0),
    Feature("uncovered_changed_lines", "verified", "changed lines no test executes"),
    Feature("mutation_score", "verified", "share of mutants on changed lines the tests kill", 1.0),
    Feature("surviving_mutants", "verified", "mutants on changed lines that no test notices"),
    Feature("verified_impacted_tests", "verified", "verified impacted tests"),
    Feature("verified_impacted_modules", "verified", "verified impacted modules"),
    Feature("verified_api_breaks", "verified", "verified API breaks"),
    Feature("verified_bug_links", "verified", "verified links to past bugs"),
    Feature("refuted_claim_ratio", "verified", "share of agent claims refuted by execution"),
    Feature("llm_risk", "llm", "LLM risk rating", 0.5),
    Feature("injection_attempts", "guard", "prompt-injection attempts in the PR"),
)
FEATURE_NAMES = tuple(f.name for f in FEATURES)
BY_NAME = {f.name: f for f in FEATURES}

# Which way a feature is allowed to push risk in the constrained model (+1 up, -1 down).
# Features left out are free: the evidence on their direction is mixed (e.g. the age of the
# touched code) or they only describe the change rather than its risk.
DIRECTIONS: dict[str, int] = {
    "log_churn": 1, "files_touched": 1, "entropy": 1, "author_is_new": 1, "recent_churn": 1,
    "complexity_delta": 1, "max_complexity": 1, "szz_defects": 1, "touches_dependencies": 1,
    "touches_config": 1, "touches_ci": 1, "verified_test_failures": 1, "uncovered_changed_lines": 1,
    "surviving_mutants": 1, "verified_api_breaks": 1, "verified_bug_links": 1, "llm_risk": 1,
    "injection_attempts": 1, "verified_impacted_modules": 1, "signature_changes": 1, "change_units": 1,
    # mature, long-unchanged code is less defect-prone (Graves et al., 2000)
    "log_code_age_days": -1,
    "log_author_prior_commits": -1, "test_only": -1, "docs_only": -1, "tests_changed": -1,
    "changed_line_coverage": -1, "mutation_score": -1,
}
GROUPS = {
    "jit": tuple(f.name for f in FEATURES if f.group == "jit"),
    "full": FEATURE_NAMES,
    "no_llm": tuple(f.name for f in FEATURES if f.group != "llm"),
    "no_verification": tuple(f.name for f in FEATURES if f.group != "verified"),
    "no_mutation": tuple(f.name for f in FEATURES if f.name not in ("mutation_score", "surviving_mutants")),
    "no_history": tuple(f.name for f in FEATURES if f.name not in ("szz_defects", "verified_bug_links")),
}


def build_features(signals: dict[str, float], verification=None, claims=None, verdicts=None, llm_risk: float | None = None, injection_attempts: int = 0) -> dict[str, float]:
    """Assemble one PR's feature vector from pipeline outputs."""
    f = {feat.name: feat.default for feat in FEATURES}
    for name in ("log_churn", "files_touched", "change_units", "entropy", "author_is_new", "recent_churn",
                 "complexity_delta", "max_complexity", "szz_defects", "touches_dependencies", "touches_config",
                 "touches_ci", "signature_changes", "test_only", "docs_only", "tests_changed", "intent_bugfix", "intent_refactor"):
        f[name] = float(signals.get(name, f[name]))
    f["log_author_prior_commits"] = math.log1p(signals.get("author_prior_commits", 0.0))
    f["log_code_age_days"] = math.log1p(max(signals.get("code_age_days", 0.0), 0.0))

    if verification is not None:
        f["verified_test_failures"] = float(len(verification.test_run.failed))
        f["changed_line_coverage"] = round(verification.changed_coverage, 4)
        f["uncovered_changed_lines"] = float(len(verification.uncovered))
        score = verification.mutation.score
        f["mutation_score"] = 1.0 if score is None else round(score, 4)
        f["surviving_mutants"] = float(len(verification.mutation.survived))

    if claims is not None and verdicts is not None:
        status = {v.claim_id: v.status for v in verdicts}
        verified = [c for c in claims if status.get(c.claim_id) is VerdictStatus.VERIFIED]
        checked = [c for c in claims if status.get(c.claim_id) in (VerdictStatus.VERIFIED, VerdictStatus.REFUTED)]
        refuted = [c for c in checked if status[c.claim_id] is VerdictStatus.REFUTED]
        f["verified_impacted_tests"] = float(sum(1 for c in verified if c.type == "test_impact"))
        f["verified_impacted_modules"] = float(sum(1 for c in verified if c.type == "module_impact"))
        f["verified_api_breaks"] = float(sum(1 for c in verified if c.type == "api_break"))
        f["verified_bug_links"] = float(sum(1 for c in verified if c.type == "history_link"))
        f["refuted_claim_ratio"] = round(len(refuted) / len(checked), 4) if checked else 0.0

    if llm_risk is not None:
        f["llm_risk"] = float(llm_risk)
    f["injection_attempts"] = float(injection_attempts)
    return f


def vectorise(features: dict[str, float], names: tuple[str, ...] = FEATURE_NAMES) -> list[float]:
    return [float(features.get(n, BY_NAME[n].default if n in BY_NAME else 0.0)) for n in names]
