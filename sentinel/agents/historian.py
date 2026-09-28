"""Historian Agent: why is the changed code the way it is, and has it hurt us before?

Deterministic output (always produced):
    * ``history_link`` claims for every SZZ-traced bug in a changed symbol
    * ``rationale`` claims for issues/PRs linked to commits that shaped a changed symbol
    * risk memories and history features (bug-fix count, prior introducers, ...)

With an LLM available, the agent also reads the linked history documents and proposes
additional cited rationale claims. The Verifier later checks every one of them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from sentinel.agents.base import AgentContext, ClaimIds, dedupe_claims
from sentinel.agents.prompts import load_prompt
from sentinel.models import Claim

log = logging.getLogger(__name__)
MAX_HISTORY_DOCS = 12


class _LLMHistoryClaim(BaseModel):
    type: str
    target: str
    assertion: str
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class _LLMHistory(BaseModel):
    claims: list[_LLMHistoryClaim] = Field(default_factory=list)
    risk_memories: list[str] = Field(default_factory=list)


@dataclass
class HistoryResult:
    claims: list[Claim]
    memories: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    documents: list[str] = field(default_factory=list)  # history doc ids considered
    mode: str = "rules"


def commit_doc_id(sha: str) -> str:
    return f"commit:{sha[:10]}"


class HistorianAgent:
    name = "historian"

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm

    def run(self, ctx: AgentContext) -> HistoryResult:
        graph, store = ctx.graph, ctx.store
        ids = ClaimIds("his")
        claims: list[Claim] = []
        memories: list[str] = []
        fix_shas: set[str] = set()
        introducers: set[str] = set()
        bug_issues: set[int] = set()
        commits_touching = 0
        authors: set[str] = set()

        for unit in ctx.source_units:
            if unit.kind == "module":
                continue
            node = ctx.node(unit)
            # SZZ: this symbol contained code that a later fix had to change
            for bug in graph.bug_history(node):
                fix = store.commit(bug["fix"])
                if fix is None:
                    continue
                fix_shas.add(fix["sha"])
                introducers.add(bug["introducing_sha"])
                evidence = [commit_doc_id(fix["sha"]), commit_doc_id(bug["introducing_sha"])]
                issue_text = ""
                if bug.get("issue"):
                    bug_issues.add(bug["issue"])
                    issue = store.issue(bug["issue"])
                    if issue:  # only cite what the tracker can back up
                        evidence.insert(0, f"issue:{bug['issue']}")
                        issue_text = f" (issue #{bug['issue']}: {issue['title']})"
                    else:
                        issue_text = f" (refs #{bug['issue']})"
                subject = fix["message"].splitlines()[0]
                claims.append(Claim(
                    claim_id=ids.next(), agent=self.name, type="history_link", target=unit.id,
                    assertion=f"{unit.qualname} was involved in bug fix {fix['sha'][:7]}{issue_text}",
                    reason=f"fixed by \"{subject}\"; the buggy lines came from commit {bug['introducing_sha'][:7]}",
                    evidence_ids=evidence,
                ))
            history = store.commits_touching_unit(unit.id)
            commits_touching += len(history)
            authors.update(c["author"] for c in history)
            fixes_here = [c for c in history if c["is_fix"]]
            fix_shas.update(c["sha"] for c in fixes_here)
            if fixes_here:
                memories.append(f"{unit.qualname} has been changed by {len(fixes_here)} bug-fix commit(s): "
                                + ", ".join(f"{c['sha'][:7]}" for c in fixes_here[:4]))
            # Issues and PR discussions that explain the current behaviour
            for commit in history:
                for number in commit["issues"]:
                    issue = store.issue(number)
                    if issue is None or any("bug" in label for label in issue["labels"]):
                        continue
                    claims.append(Claim(
                        claim_id=ids.next(), agent=self.name, type="rationale", target=unit.id,
                        assertion=f"{unit.qualname} implements issue #{number}: {issue['title']}",
                        reason=(issue["body"] or "")[:240],
                        evidence_ids=[f"issue:{number}", commit_doc_id(commit["sha"])],
                    ))

        features = {
            "szz_bug_fixes": float(len(fix_shas)),
            "szz_introducers": float(len(introducers)),
            "bug_issues": float(len(bug_issues)),
            "history_commits": float(commits_touching),
            "history_authors": float(len(authors)),
        }
        seeds = [ctx.node(u) for u in ctx.source_units if u.kind != "module"]
        query = " ".join(u.symbol for u in ctx.source_units) + " " + ctx.pr.title
        try:
            docs = ctx.retriever.search_history(query, seeds=seeds, top_k=MAX_HISTORY_DOCS)
        except Exception as exc:
            log.warning("history retrieval failed: %s", exc)
            docs = []

        mode = "rules"
        if self.use_llm and ctx.llm_allowed and docs and seeds:
            try:
                extra, llm_memories = self._llm_claims(ctx, docs, ids)
                claims.extend(extra)
                memories.extend(m for m in llm_memories if m not in memories)
                mode = "llm"
            except Exception as exc:
                log.warning("LLM historian failed: %s", exc)
                ctx.guard("llm", "mid", "info", f"historian fell back to rules: {exc}")
        return HistoryResult(dedupe_claims(claims), memories, features, [d.id for d in docs], mode)

    def _llm_claims(self, ctx: AgentContext, docs, ids: ClaimIds) -> tuple[list[Claim], list[str]]:
        prompt = load_prompt("historian", ctx.cfg.get("prompts.historian"))
        ctx.prompt_versions["historian"] = prompt.id
        changes = "\n".join(f"- {u.id} ({u.change_type})" for u in ctx.source_units if u.kind != "module")
        history = "\n\n".join(f"[{d.id}]\n{d.text[:900]}" for d in docs)
        system, user = prompt.render(title=ctx.pr.title, body=ctx.pr.body[:1500], changes=changes, history=history)
        parsed, _, problems = ctx.llm.structured("historian", system, user, _LLMHistory, diff_lines=ctx.diff_lines, change_units=len(ctx.units))
        for problem in problems:
            ctx.guard("schema", "mid", "warn", f"historian output failed validation: {problem}")
        if parsed is None:
            return [], []
        allowed_docs = {d.id for d in docs}
        units = {u.id for u in ctx.units}
        claims = []
        for raw in parsed.claims:
            cited = [e for e in raw.evidence_ids if e in allowed_docs]
            if raw.type not in ("history_link", "rationale") or not cited or raw.target not in units:
                ctx.guard("schema", "mid", "info", f"discarded historian claim on {raw.target!r} (bad type, target or evidence)")
                continue
            claims.append(Claim(claim_id=ids.next(), agent=self.name, type=raw.type, target=raw.target,
                                assertion=raw.assertion, reason=raw.reason, evidence_ids=cited))
        return claims, parsed.risk_memories[:5]
