"""Test Selector: which tests to run for this change, within a time budget.

Candidates, strongest first:
    1. tests recorded (coverage map) executing a changed symbol
    2. tests named in Impact claims
    3. tests that reach the change through the call graph
    4. tests modified by the PR itself
Parametrised variants are expanded to concrete pytest node ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sentinel.agents.base import AgentContext
from sentinel.agents.impact import ImpactEvidence, test_target
from sentinel.models import Claim
from sentinel.retrieval.graph import is_test_path

PRIORITY = {"coverage": 0, "claim": 1, "static": 2, "changed": 1}
DEFAULT_TEST_SECONDS = 0.5


@dataclass
class Selection:
    test_ids: list[str]
    reasons: dict[str, str] = field(default_factory=dict)
    total_available: int = 0
    estimated_s: float = 0.0
    full_suite_s: float = 0.0
    dropped_for_budget: list[str] = field(default_factory=list)

    @property
    def time_saved_ratio(self) -> float:
        if not self.full_suite_s:
            return 0.0
        return max(0.0, 1 - self.estimated_s / self.full_suite_s)


def expand(target: str, known: dict[str, dict]) -> list[str]:
    """Function-level test id -> concrete node ids (parametrised variants included)."""
    if target in known:
        return [target]
    prefix = target + "["
    return sorted(t for t in known if t.startswith(prefix) or t == target)


def select_tests(ctx: AgentContext, evidence: ImpactEvidence | None, claims: list[Claim], budget_s: float | None = None) -> Selection:
    known = {t["test_id"]: t for t in ctx.store.all_tests()}
    graph = ctx.graph
    ranked: dict[str, tuple[int, str]] = {}

    def offer(target: str, source: str) -> None:
        for node_id in expand(target, known) or ([target] if not known else []):
            current = ranked.get(node_id)
            if current is None or PRIORITY[source] < current[0]:
                ranked[node_id] = (PRIORITY[source], source)

    if evidence is not None:
        for test in evidence.covering_tests:
            offer(test_target(graph, test), "coverage")
        for test, (distance, _) in evidence.static_tests.items():
            if distance <= 3:
                offer(test_target(graph, test), "static")
    for claim in claims:
        if claim.type == "test_impact":
            offer(claim.target, "claim")
    for change in ctx.changes:
        if is_test_path(change.path) and change.status != "removed":
            for test_id in known:
                if test_id.startswith(change.path + "::"):
                    offer(test_id, "changed")
            if not known:
                offer(change.path, "changed")

    budget_s = budget_s if budget_s is not None else float(ctx.cfg.get("verify.selection_budget_s", 300))
    ordered = sorted(ranked, key=lambda t: (ranked[t][0], t))
    selected, dropped, spent = [], [], 0.0
    for test_id in ordered:
        cost = known.get(test_id, {}).get("duration") or DEFAULT_TEST_SECONDS
        if spent + cost > budget_s and ranked[test_id][0] > 0:
            dropped.append(test_id)
            continue
        selected.append(test_id)
        spent += cost
    return Selection(
        test_ids=selected,
        reasons={t: ranked[t][1] for t in selected},
        total_available=len(known),
        estimated_s=round(spent, 3),
        full_suite_s=round(sum((t.get("duration") or DEFAULT_TEST_SECONDS) for t in known.values()), 3),
        dropped_for_budget=dropped,
    )
