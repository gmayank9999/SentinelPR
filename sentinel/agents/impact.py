"""Impact Agent: which tests and modules does this change affect, and does it break an API?

Evidence is gathered deterministically first:

* **coverage** — tests whose recorded execution touches a changed symbol (``cov:<test>``)
* **call graph** — callers up to two hops away, and tests that reach the change statically
  (``graph:calls:a->b``)
* **retrieval** — related chunks from hybrid search (``chunk:<path>#L..``)

In ``llm`` mode the model sees that evidence and proposes claims; claims that cite ids it was
not given are discarded as hallucinated. In ``rules`` mode (no LLM available, or chosen for a
baseline) claims are generated straight from the evidence. ``static`` and ``lexical`` modes
reproduce the call-graph and name-search baselines used in the evaluation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from sentinel.agents.base import AgentContext, ClaimIds, dedupe_claims
from sentinel.agents.prompts import load_prompt
from sentinel.models import Claim
from sentinel.retrieval.graph import CodeGraph, is_test_path, node_to_unit

log = logging.getLogger(__name__)

MODULE_HOPS = 2
STATIC_TEST_DEPTH = 4
MAX_PROMPT_TESTS = 60


class _LLMClaim(BaseModel):
    type: str
    target: str
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class _LLMImpact(BaseModel):
    claims: list[_LLMClaim] = Field(default_factory=list)
    risk_rating: str = "medium"
    risk_reason: str = ""


@dataclass
class ImpactEvidence:
    # test fn node -> changed nodes whose *changed lines* it executed (line-level, indexed revision)
    line_tests: dict[str, list[str]] = field(default_factory=dict)
    # test fn node -> changed nodes it executes anywhere (symbol-level; used for safe selection)
    covering_tests: dict[str, list[str]] = field(default_factory=dict)
    static_tests: dict[str, tuple[int, list[str]]] = field(default_factory=dict)  # test node -> (distance, path)
    modules: dict[str, list[str]] = field(default_factory=dict)  # file -> evidence ids
    api_changes: dict[str, list[str]] = field(default_factory=dict)  # changed node -> caller nodes
    retrieved: list = field(default_factory=list)
    ids: set[str] = field(default_factory=set)


@dataclass
class ImpactResult:
    claims: list[Claim]
    mode: str
    llm_risk: float | None = None
    llm_risk_reason: str = ""
    evidence: ImpactEvidence | None = None
    discarded: list[str] = field(default_factory=list)


def test_target(graph: CodeGraph, node: str) -> str:
    """A test function node -> the test id used in claims (``tests/x.py::test_y``)."""
    data = graph.node(node)
    return f"{data['path']}::{data['qualname'].replace('.', '::')}"


class ImpactAgent:
    name = "impact"

    def __init__(self, mode: str = "auto"):
        self.mode = mode

    # evidence -------------------------------------------------------------------
    def gather(self, ctx: AgentContext) -> ImpactEvidence:
        graph = ctx.graph
        ev = ImpactEvidence()
        for unit in ctx.source_units:
            node = ctx.node(unit)
            if unit.kind == "module":
                continue
            symbol_tests = graph.covering_tests(node)
            for test in symbol_tests:
                ev.covering_tests.setdefault(test, []).append(node)
                ev.ids.add(f"covsym:{test_target(graph, test)}")
            # Lines the PR modified still have their old numbers, which the coverage map uses.
            precise = ctx.store.tests_covering(unit.file, unit.removed_lines) if unit.removed_lines else {}
            if precise:
                for test_id in precise:
                    test = graph.test_node_for(test_id)
                    if test is not None and node not in ev.line_tests.get(test, []):
                        ev.line_tests.setdefault(test, []).append(node)
                        ev.ids.add(f"cov:{test_target(graph, test)}")
            elif not unit.removed_lines:
                # Pure additions have no old lines; fall back to tests of the enclosing symbol.
                for test in symbol_tests:
                    ev.line_tests.setdefault(test, []).append(node)
                    ev.ids.add(f"cov:{test_target(graph, test)}")
            for test, distance in graph.static_tests_for(node, STATIC_TEST_DEPTH).items():
                path = graph.call_path(test, node, STATIC_TEST_DEPTH) or [test, node]
                if test not in ev.static_tests or distance < ev.static_tests[test][0]:
                    ev.static_tests[test] = (distance, path)
                ev.ids.update(CodeGraph.evidence("calls", a, b) for a, b in zip(path, path[1:]))
            callers = graph.callers(node, MODULE_HOPS)
            for caller, _ in sorted(callers.items(), key=lambda kv: kv[1]):
                data = graph.node(caller)
                if not data or data.get("is_test") or is_test_path(data.get("path", "")):
                    continue
                path = graph.call_path(caller, node, MODULE_HOPS) or [caller, node]
                edge_ids = [CodeGraph.evidence("calls", a, b) for a, b in zip(path, path[1:])]
                ev.modules.setdefault(data["path"], [])
                ev.modules[data["path"]].extend(e for e in edge_ids if e not in ev.modules[data["path"]])
                ev.ids.update(edge_ids)
            if unit.signature_changed:
                external = [c for c, d in graph.callers(node, 1).items() if not graph.node(c).get("is_test")]
                ev.api_changes[node] = external
                ev.ids.add(f"sig:{unit.id}")
        # the changed modules themselves are trivially impacted; do not claim them
        for unit in ctx.units:
            ev.modules.pop(unit.file, None)

        seeds = [ctx.node(u) for u in ctx.source_units if u.kind != "module"]
        query = " ".join(u.symbol for u in ctx.source_units) + " " + ctx.pr.title
        try:
            ev.retrieved = ctx.retriever.search_code(query, seeds=seeds)
        except Exception as exc:  # retrieval is supporting context; never fatal
            log.warning("retrieval failed: %s", exc)
            ev.retrieved = []
        ev.ids.update(r.id for r in ev.retrieved)
        return ev

    # claims ---------------------------------------------------------------------
    def rule_claims(self, ctx: AgentContext, ev: ImpactEvidence, ids: ClaimIds) -> list[Claim]:
        graph = ctx.graph
        claims: list[Claim] = []
        for test, changed in sorted(ev.line_tests.items()):
            target = test_target(graph, test)
            changed_names = ", ".join(sorted({graph.node(c)["qualname"] for c in changed}))
            claims.append(Claim(claim_id=ids.next(), agent=self.name, type="test_impact", target=target,
                                reason=f"recorded coverage shows it executes the changed lines of {changed_names}", evidence_ids=[f"cov:{target}"]))
        for test, (distance, path) in sorted(ev.static_tests.items(), key=lambda kv: kv[1][0]):
            if test in ev.covering_tests or distance > 3:
                continue
            target = test_target(graph, test)
            chain = " -> ".join(graph.node(n)["qualname"] for n in path)
            claims.append(Claim(claim_id=ids.next(), agent=self.name, type="test_impact", target=target,
                                reason=f"reaches the change through {chain}",
                                evidence_ids=[CodeGraph.evidence("calls", a, b) for a, b in zip(path, path[1:])]))
        for module, evidence in sorted(ev.modules.items()):
            claims.append(Claim(claim_id=ids.next(), agent=self.name, type="module_impact", target=module,
                                reason="calls into changed code", evidence_ids=evidence[:4]))
        for node, callers in ev.api_changes.items():
            if callers:
                unit = node_to_unit(node)
                claims.append(Claim(claim_id=ids.next(), agent=self.name, type="api_break", target=unit,
                                    reason=f"signature changed and {len(callers)} caller(s) depend on it",
                                    evidence_ids=[f"sig:{unit}"] + [CodeGraph.evidence("calls", c, node) for c in callers[:3]]))
        return claims

    def static_claims(self, ctx: AgentContext, ev: ImpactEvidence, ids: ClaimIds) -> list[Claim]:
        """B-static baseline: call-graph reachability only, no coverage."""
        graph = ctx.graph
        claims = []
        for test, (_, path) in sorted(ev.static_tests.items()):
            claims.append(Claim(claim_id=ids.next(), agent="static", type="test_impact", target=test_target(graph, test),
                                reason="statically reachable", evidence_ids=[CodeGraph.evidence("calls", a, b) for a, b in zip(path, path[1:])]))
        for module, evidence in sorted(ev.modules.items()):
            claims.append(Claim(claim_id=ids.next(), agent="static", type="module_impact", target=module,
                                reason="statically reachable", evidence_ids=evidence[:4]))
        return claims

    def lexical_claims(self, ctx: AgentContext, ids: ClaimIds) -> list[Claim]:
        """B-lex baseline: any test or module that mentions a changed symbol's name."""
        search = ctx.index.code_search
        changed_files = {u.file for u in ctx.units}
        claims: dict[tuple[str, str], Claim] = {}
        for unit in ctx.source_units:
            if unit.kind == "module":
                continue
            for hit in search.references(unit.symbol):
                if hit.path in changed_files and not is_test_path(hit.path):
                    continue
                if is_test_path(hit.path):
                    info = ctx.index.chunk_at(hit.path, hit.line)
                    if info is None or not info.symbol or not info.symbol.split(".")[-1].startswith("test"):
                        continue
                    key = ("test_impact", f"{hit.path}::{info.symbol.replace('.', '::')}")
                else:
                    key = ("module_impact", hit.path)
                claims.setdefault(key, Claim(claim_id=ids.next(), agent="lexical", type=key[0], target=key[1],
                                             reason=f"mentions {unit.symbol}", evidence_ids=[hit.evidence_id]))
        return list(claims.values())

    def llm_claims(self, ctx: AgentContext, ev: ImpactEvidence, ids: ClaimIds, feedback: str = "") -> tuple[list[Claim], float | None, str, list[str]]:
        graph = ctx.graph
        prompt = load_prompt("impact", ctx.cfg.get("prompts.impact"))
        ctx.prompt_versions["impact"] = prompt.id
        candidates = {}
        for test in list(ev.line_tests)[:MAX_PROMPT_TESTS]:
            target = test_target(graph, test)
            candidates[target] = f"{target}  (executes the changed lines; evidence: cov:{target})"
        for test in ev.covering_tests:
            target = test_target(graph, test)
            if target not in candidates and len(candidates) < MAX_PROMPT_TESTS:
                candidates[target] = f"{target}  (executes the changed function, not necessarily the changed lines; evidence: covsym:{target})"
        for test, (distance, path) in sorted(ev.static_tests.items(), key=lambda kv: kv[1][0]):
            target = test_target(graph, test)
            if target not in candidates and len(candidates) < MAX_PROMPT_TESTS:
                edges = ", ".join(CodeGraph.evidence("calls", a, b) for a, b in zip(path, path[1:]))
                candidates[target] = f"{target}  (static, distance {distance}; evidence: {edges})"
        neighbourhood = []
        for module, evidence in ev.modules.items():
            neighbourhood.append(f"- module {module}: {', '.join(evidence[:4])}")
        for node, callers in ev.api_changes.items():
            neighbourhood.append(f"- signature changed: sig:{node_to_unit(node)} (callers: {', '.join(graph.node(c)['qualname'] for c in callers[:5]) or 'none'})")
        retrieved = "\n\n".join(f"[{r.id}]\n{r.text[:800]}" for r in ev.retrieved[:6])
        system, user = prompt.render(
            title=ctx.pr.title, body=ctx.pr.body[:2000],
            changes=_describe_changes(ctx), neighbourhood="\n".join(neighbourhood) or "(no callers found)",
            candidate_tests="\n".join(candidates.values()) or "(none)", retrieved=retrieved or "(none)",
        )
        if feedback:
            user += f"\n\nSOME OF YOUR EARLIER CLAIMS WERE REFUTED BY EXECUTION:\n{feedback}\nRevise your claims."
        parsed, _, problems = ctx.llm.structured(
            "impact", system, user, _LLMImpact, diff_lines=ctx.diff_lines, change_units=len(ctx.units)
        )
        for problem in problems:
            ctx.guard("schema", "mid", "warn", f"impact output failed validation: {problem}")
        if parsed is None:
            return [], None, "", ["impact reply could not be parsed"]

        allowed = set(ev.ids)
        claims, discarded = [], []
        for raw in parsed.claims:
            if raw.type not in ("test_impact", "module_impact", "api_break"):
                discarded.append(f"unknown claim type {raw.type!r}")
                continue
            cited = [e for e in raw.evidence_ids if e in allowed]
            if not cited:
                discarded.append(f"{raw.type} {raw.target}: no valid evidence ({raw.evidence_ids})")
                continue
            claims.append(Claim(claim_id=ids.next(), agent=self.name, type=raw.type, target=raw.target, reason=raw.reason, evidence_ids=cited))
        if discarded:
            ctx.guard("schema", "mid", "info", f"discarded {len(discarded)} impact claim(s) without valid evidence")
        rating = {"low": 0.2, "medium": 0.5, "high": 0.8}.get(parsed.risk_rating.lower().strip())
        return claims, rating, parsed.risk_reason, discarded

    # entry point ------------------------------------------------------------------
    def run(self, ctx: AgentContext, feedback: str = "") -> ImpactResult:
        ev = self.gather(ctx)
        ids = ClaimIds("imp")
        mode = self.mode
        if mode == "auto":
            mode = "llm" if ctx.llm_allowed else "rules"
        if mode == "static":
            return ImpactResult(self.static_claims(ctx, ev, ids), mode, evidence=ev)
        if mode == "lexical":
            return ImpactResult(self.lexical_claims(ctx, ids), mode, evidence=ev)
        if mode == "llm":
            try:
                claims, rating, reason, discarded = self.llm_claims(ctx, ev, ids, feedback)
                if claims:
                    # Coverage-backed tests are facts, not opinions: always keep them as claims too.
                    rule_tests = [c for c in self.rule_claims(ctx, ev, ClaimIds("imp-cov")) if c.evidence_ids[0].startswith("cov:")]
                    merged = dedupe_claims(claims + rule_tests)
                    for i, claim in enumerate(merged, start=1):
                        claim.claim_id = f"imp-{i:03d}"
                    return ImpactResult(merged, "llm", rating, reason, ev, discarded)
            except Exception as exc:
                log.warning("LLM impact analysis failed, using rules: %s", exc)
                ctx.guard("llm", "mid", "info", f"impact agent fell back to rules: {exc}")
        return ImpactResult(dedupe_claims(self.rule_claims(ctx, ev, ids)), "rules", evidence=ev)


def _describe_changes(ctx: AgentContext, max_units: int = 12) -> str:
    lines = []
    for unit in ctx.units[:max_units]:
        header = f"- {unit.id} ({unit.kind}, {unit.change_type})"
        if unit.signature_changed:
            header += f"\n    signature: {unit.old_signature}  ->  {unit.new_signature}"
        source = ctx.index.sources.get(unit.file, "")
        if unit.changed_lines and source:
            src_lines = source.splitlines()
            snippet = [f"    {n:>4}+ {src_lines[n - 1]}" for n in unit.changed_lines[:12] if 0 < n <= len(src_lines)]
            header += "\n" + "\n".join(snippet)
        lines.append(header)
    if len(ctx.units) > max_units:
        lines.append(f"- ... and {len(ctx.units) - max_units} more")
    return "\n".join(lines)
