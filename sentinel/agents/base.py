"""What every agent receives: the change, the repository knowledge and the LLM client."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from sentinel.config import Config
from sentinel.models import ChangeUnit, Claim, FileChange, GuardEvent
from sentinel.retrieval.graph import is_test_path, unit_to_node


@dataclass
class PullRequest:
    number: int | None
    title: str
    body: str
    author: str = ""
    base_sha: str = ""
    head_sha: str | None = None
    labels: list[str] = field(default_factory=list)
    is_fork: bool = False
    commit_messages: list[str] = field(default_factory=list)


@dataclass
class AgentContext:
    cfg: Config
    store: object  # sentinel.data.store.Store
    index: object  # sentinel.retrieval.hybrid.RetrievalIndex
    retriever: object  # sentinel.retrieval.hybrid.HybridRetriever
    llm: object  # sentinel.llm.client.LLMClient
    pr: PullRequest
    changes: list[FileChange]
    units: list[ChangeUnit]
    diff: object = None  # sentinel.retrieval.diff_parser.DiffContext
    all_changed_paths: list[str] = field(default_factory=list)  # repository-wide, not just the project
    guard_events: list[GuardEvent] = field(default_factory=list)
    prompt_versions: dict[str, str] = field(default_factory=dict)

    @property
    def graph(self):
        return self.index.graph

    @property
    def source_units(self) -> list[ChangeUnit]:
        """Changed units in application code (tests excluded)."""
        return [u for u in self.units if not is_test_path(u.file)]

    @property
    def diff_lines(self) -> int:
        return sum(c.additions + c.deletions for c in self.changes)

    def read_old(self, path: str) -> str | None:
        return self.diff.read_old(path) if self.diff is not None else None

    def read_new(self, path: str) -> str | None:
        if self.diff is not None:
            return self.diff.read_new(path)
        return self.index.sources.get(path)

    def node(self, unit: ChangeUnit) -> str:
        return unit_to_node(unit.id)

    def guard(self, guard: str, stage: str, severity: str, message: str, location: str | None = None) -> None:
        self.guard_events.append(GuardEvent(guard=guard, stage=stage, severity=severity, message=message, location=location))

    @property
    def llm_allowed(self) -> bool:
        """Fork PRs run without secrets; they only get the local model, if any."""
        if not self.llm.available:
            return False
        if self.pr.is_fork:
            return any(p.available for n, p in self.llm.providers.items() if n == "ollama")
        return True


class ClaimIds:
    """Sequential, readable claim ids: imp-001, his-002, ..."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self._counter = itertools.count(1)

    def next(self) -> str:
        return f"{self.prefix}-{next(self._counter):03d}"


def dedupe_claims(claims: list[Claim]) -> list[Claim]:
    seen: dict[tuple[str, str], Claim] = {}
    for claim in claims:
        key = (claim.type, claim.target)
        if key in seen:
            merged = seen[key]
            merged.evidence_ids = list(dict.fromkeys(merged.evidence_ids + claim.evidence_ids))
        else:
            seen[key] = claim
    return list(seen.values())
