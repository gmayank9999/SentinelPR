"""Data contracts shared across the pipeline.

Agents talk to the rest of the system only through these models; anything an LLM
returns is validated against them before it is allowed further down the pipeline.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ClaimType = Literal["test_impact", "module_impact", "api_break", "history_link", "rationale"]
Decision = Literal["PASS", "CANARY", "BLOCK"]


class FileChange(BaseModel):
    """One file in a diff. Line numbers are 1-based; ``added_lines`` use the new numbering,
    ``removed_lines`` the old numbering."""

    path: str
    old_path: str | None = None
    status: Literal["added", "removed", "modified", "renamed"] = "modified"
    added_lines: list[int] = Field(default_factory=list)
    removed_lines: list[int] = Field(default_factory=list)
    is_binary: bool = False

    @property
    def additions(self) -> int:
        return len(self.added_lines)

    @property
    def deletions(self) -> int:
        return len(self.removed_lines)


class ChangeUnit(BaseModel):
    """A function, method or class touched by the change."""

    id: str  # "<path>::<qualname>"
    file: str
    symbol: str
    qualname: str
    kind: Literal["function", "method", "class", "module"]
    change_type: Literal["added", "removed", "modified", "renamed"]
    old_range: tuple[int, int] | None = None
    new_range: tuple[int, int] | None = None
    changed_lines: list[int] = Field(default_factory=list)  # new numbering
    removed_lines: list[int] = Field(default_factory=list)  # old numbering
    old_signature: str | None = None
    new_signature: str | None = None

    @property
    def signature_changed(self) -> bool:
        return (
            self.old_signature is not None
            and self.new_signature is not None
            and self.old_signature != self.new_signature
        )


class Claim(BaseModel):
    claim_id: str
    agent: str
    type: ClaimType
    target: str
    assertion: str = "affected"
    reason: str = ""
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("target")
    @classmethod
    def _target_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("claim target must not be empty")
        return value.strip()


class VerdictStatus(str, Enum):
    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    UNVERIFIABLE = "UNVERIFIABLE"


class Verdict(BaseModel):
    claim_id: str
    status: VerdictStatus
    method: str
    evidence: list[str] = Field(default_factory=list)
    detail: str = ""


class TestOutcome(BaseModel):
    __test__ = False  # not a pytest test class

    nodeid: str
    outcome: Literal["passed", "failed", "error", "skipped"]
    duration_s: float = 0.0
    message: str = ""


class TestRun(BaseModel):
    __test__ = False

    selected: list[str] = Field(default_factory=list)
    outcomes: list[TestOutcome] = Field(default_factory=list)
    duration_s: float = 0.0
    total_available: int = 0
    collection_error: str | None = None

    @property
    def failed(self) -> list[TestOutcome]:
        return [o for o in self.outcomes if o.outcome in ("failed", "error")]

    @property
    def passed(self) -> list[TestOutcome]:
        return [o for o in self.outcomes if o.outcome == "passed"]


class Mutant(BaseModel):
    id: str
    file: str
    line: int
    operator: str
    original: str
    mutated: str
    symbol: str | None = None
    status: Literal["killed", "survived", "timeout", "error", "pending"] = "pending"
    killed_by: list[str] = Field(default_factory=list)


class MutationReport(BaseModel):
    mutants: list[Mutant] = Field(default_factory=list)
    duration_s: float = 0.0

    @property
    def evaluated(self) -> list[Mutant]:
        return [m for m in self.mutants if m.status in ("killed", "survived", "timeout")]

    @property
    def survived(self) -> list[Mutant]:
        return [m for m in self.mutants if m.status == "survived"]

    @property
    def score(self) -> float | None:
        evaluated = self.evaluated
        if not evaluated:
            return None
        # A timeout means the tests noticed something was wrong (usually an infinite loop).
        return sum(1 for m in evaluated if m.status != "survived") / len(evaluated)


class Contribution(BaseModel):
    feature: str
    value: float
    contribution: float
    label: str = ""


class RiskAssessment(BaseModel):
    probability: float
    model: str
    calibrated: bool = True
    contributions: list[Contribution] = Field(default_factory=list)
    features: dict[str, float] = Field(default_factory=dict)


class GuardEvent(BaseModel):
    guard: str
    stage: Literal["in", "mid", "out"]
    severity: Literal["info", "warn", "block"]
    message: str
    location: str | None = None


class TraceEvent(BaseModel):
    node: str
    start: float
    end: float
    status: Literal["ok", "error", "skipped"] = "ok"
    tokens: int = 0
    model: str | None = None
    retries: int = 0
    note: str = ""


class LLMCall(BaseModel):
    task: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    cache_hit: bool = False
    tier: str = "medium"
    error: str | None = None
