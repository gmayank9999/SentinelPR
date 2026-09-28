"""LangGraph state. Only plain, serialisable data lives here; heavyweight objects (the index,
the LLM client, agent outputs with rich types) are held by the pipeline runtime."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict


def lowest(current: int, update: int) -> int:
    """Reducer for values reported by parallel nodes: keep the smallest."""
    return min(current, update)


class SentinelState(TypedDict, total=False):
    pr: dict  # number, base_sha, head_sha, sanitised title/body
    change_units: list[dict]
    impact_claims: list[dict]
    history_claims: list[dict]
    signals: dict
    selected_tests: list[str]
    verdicts: list[dict]
    risk: float | None
    risk_explanation: str | None
    decision: Literal["PASS", "CANARY", "BLOCK"] | None
    guard_flags: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]
    budget_tokens_left: Annotated[int, lowest]
    rejected: bool
    impact_retried: bool
    patch_requested: bool
