"""Complexity-based model routing.

Each LLM task gets a complexity score in [0, 1] from the task type, the size of the change
and the amount of context. Easy tasks go to a small local model, medium ones to a
Flash-class model, hard ones to the largest free model. When the run's token budget runs
low, everything is pushed down to the cheapest tier.
"""

from __future__ import annotations

from dataclasses import dataclass

TASK_BASE = {
    "intent": 0.1,
    "explain": 0.2,
    "rationale_judge": 0.35,
    "qa": 0.45,
    "historian": 0.45,
    "impact": 0.55,
    "patch": 0.8,
}

TIERS = ("easy", "medium", "hard")


@dataclass
class RouteDecision:
    tier: str
    score: float
    reason: str


def complexity(task: str, *, diff_lines: int = 0, change_units: int = 0, context_tokens: int = 0) -> float:
    score = TASK_BASE.get(task, 0.5)
    score += min(diff_lines / 800, 1.0) * 0.2
    score += min(change_units / 12, 1.0) * 0.15
    score += min(context_tokens / 12000, 1.0) * 0.1
    return round(min(score, 1.0), 3)


def route(task: str, *, diff_lines: int = 0, change_units: int = 0, context_tokens: int = 0,
          budget_left: int | None = None, low_budget: int = 8000, enabled: bool = True) -> RouteDecision:
    score = complexity(task, diff_lines=diff_lines, change_units=change_units, context_tokens=context_tokens)
    if not enabled:
        return RouteDecision("medium", score, "routing disabled")
    if budget_left is not None and budget_left < low_budget:
        return RouteDecision("easy", score, f"token budget low ({budget_left} left)")
    if score < 0.35:
        return RouteDecision("easy", score, "simple task")
    if score < 0.7:
        return RouteDecision("medium", score, "moderate task")
    return RouteDecision("hard", score, "complex task")
