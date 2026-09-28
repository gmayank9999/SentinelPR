"""One checker per claim type. Each returns a Verdict built from execution or repository facts.

| claim           | VERIFIED when                                                         |
|-----------------|------------------------------------------------------------------------|
| test_impact     | the test executes a changed line on the head, fails on the head, or     |
|                 | kills a mutant of a changed line                                        |
| module_impact   | the module reaches changed code through calls AND a test that executes |
|                 | changed lines also executes the module                                  |
| api_break       | the signature became incompatible and callers exist, or callers' tests |
|                 | fail on the head                                                        |
| history_link    | cited commits/issues exist and the commits touch the claimed symbol     |
| rationale       | citations exist and support the statement (LLM judge or lexical check)  |
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from sentinel.agents.prompts import load_prompt
from sentinel.models import ChangeUnit, Claim, MutationReport, TestRun, Verdict, VerdictStatus
from sentinel.retrieval.graph import CodeGraph, is_test_path, unit_to_node
from sentinel.retrieval.text import tokenize

log = logging.getLogger(__name__)

V, R, U = VerdictStatus.VERIFIED, VerdictStatus.REFUTED, VerdictStatus.UNVERIFIABLE
ENTAILMENT_SUPPORT = 0.6
ENTAILMENT_REFUTE = 0.25


class _Judgement(BaseModel):
    supported: bool
    quote: str = ""


@dataclass
class Facts:
    """Everything observed by executing the head revision."""

    units: list[ChangeUnit]
    test_run: TestRun
    line_tests: dict[tuple[str, int], set[str]]
    mutation: MutationReport
    graph: CodeGraph
    store: object
    llm: object = None
    llm_allowed: bool = False
    changed_lines: dict[str, set[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.changed_lines:
            for unit in self.units:
                if not is_test_path(unit.file):
                    self.changed_lines.setdefault(unit.file, set()).update(unit.changed_lines)
        self.executed_by: dict[str, set[tuple[str, int]]] = {}
        for key, tests in self.line_tests.items():
            for test in tests:
                self.executed_by.setdefault(test, set()).add(key)
        self.outcomes = {o.nodeid: o for o in self.test_run.outcomes}
        self.kills: dict[str, list[str]] = {}
        for mutant in self.mutation.mutants:
            for test in mutant.killed_by:
                self.kills.setdefault(test, []).append(mutant.id)

    def node_ids(self, target: str) -> list[str]:
        """Claim targets are function-level ids; runs report concrete (parametrised) node ids."""
        return [n for n in self.outcomes if n == target or n.startswith(target + "[")]

    def changed_lines_hit(self, test_id: str) -> list[tuple[str, int]]:
        return sorted(k for k in self.executed_by.get(test_id, ()) if k[1] in self.changed_lines.get(k[0], ()))

    def tests_hitting_changes(self) -> set[str]:
        return {t for t in self.executed_by if self.changed_lines_hit(t)}


def check_test_impact(claim: Claim, facts: Facts) -> Verdict:
    ids = facts.node_ids(claim.target)
    if not ids:
        return Verdict(claim_id=claim.claim_id, status=U, method="execution", detail="test was not run (unknown or outside the time budget)")
    evidence, reasons = [], []
    for node_id in ids:
        hits = facts.changed_lines_hit(node_id)
        if hits:
            evidence.append(f"exec:{node_id}->{hits[0][0]}#L{hits[0][1]}")
            reasons.append(f"executes {len(hits)} changed line(s)")
        if facts.outcomes[node_id].outcome in ("failed", "error"):
            evidence.append(f"fail:{node_id}")
            reasons.append("fails on the head revision")
        if node_id in facts.kills:
            evidence.append(f"kill:{facts.kills[node_id][0]}")
            reasons.append(f"kills {len(facts.kills[node_id])} mutant(s) of changed lines")
    if evidence:
        return Verdict(claim_id=claim.claim_id, status=V, method="execution", evidence=sorted(set(evidence))[:6], detail="; ".join(dict.fromkeys(reasons)))
    return Verdict(claim_id=claim.claim_id, status=R, method="execution", evidence=[f"run:{i}" for i in ids[:3]],
                   detail="ran the test on the head: it never executes a changed line, passes, and kills no mutant")


def check_module_impact(claim: Claim, facts: Facts) -> Verdict:
    module = claim.target
    changed_nodes = [unit_to_node(u.id) for u in facts.units if u.kind != "module" and not is_test_path(u.file)]
    reachable_via = None
    for node in changed_nodes:
        for caller in facts.graph.callers(node, depth=4):
            if facts.graph.node(caller).get("path") == module:
                reachable_via = (caller, node)
                break
        if reachable_via:
            break
    if reachable_via is None:
        return Verdict(claim_id=claim.claim_id, status=R, method="call-graph", detail=f"no call path from {module} into the changed code")
    path = facts.graph.call_path(*reachable_via) or list(reachable_via)
    edge_ids = [CodeGraph.evidence("calls", a, b) for a, b in zip(path, path[1:])]
    exercising = [t for t in facts.tests_hitting_changes() if any(f == module for f, _ in facts.executed_by.get(t, ()))]
    if exercising:
        return Verdict(claim_id=claim.claim_id, status=V, method="call-graph+execution", evidence=edge_ids[:3] + [f"exec:{exercising[0]}"],
                       detail=f"reachable, and {len(exercising)} test(s) execute both the module and changed lines")
    return Verdict(claim_id=claim.claim_id, status=U, method="call-graph", evidence=edge_ids[:3],
                   detail="reachable by calls, but no executed test exercises both")


def _params(signature: str | None) -> list[tuple[str, bool]] | None:
    """(name, has_default) for positional-or-keyword parameters of a ``def`` header."""
    if not signature:
        return None
    header = signature.split("->")[0].strip()
    if header.startswith("async "):
        header = header[6:]
    try:
        tree = ast.parse(header + ": pass")
    except SyntaxError:
        return None
    fn = tree.body[0]
    if not isinstance(fn, ast.FunctionDef):
        return None
    args = fn.args
    positional = args.posonlyargs + args.args
    defaults = [False] * (len(positional) - len(args.defaults)) + [True] * len(args.defaults)
    params = [(a.arg, d) for a, d in zip(positional, defaults) if a.arg not in ("self", "cls")]
    params += [(a.arg, d is not None) for a, d in zip(args.kwonlyargs, args.kw_defaults)]
    return params


def signature_incompatible(old: str | None, new: str | None) -> str | None:
    before, after = _params(old), _params(new)
    if before is None or after is None:
        return None
    old_names = [n for n, _ in before]
    new_required = [n for n, d in after if not d]
    removed = [n for n in old_names if n not in {n for n, _ in after}]
    added_required = [n for n in new_required if n not in old_names]
    reordered = [n for n in old_names if n in [m for m, _ in after]]
    if removed:
        return f"parameter(s) removed: {', '.join(removed)}"
    if added_required:
        return f"new required parameter(s): {', '.join(added_required)}"
    if reordered != [m for m, _ in after if m in old_names]:
        return "parameters reordered"
    return None


def check_api_break(claim: Claim, facts: Facts) -> Verdict:
    unit = next((u for u in facts.units if u.id == claim.target), None)
    if unit is None:
        return Verdict(claim_id=claim.claim_id, status=U, method="static", detail="target is not a changed symbol")
    node = unit_to_node(unit.id)
    callers = [c for c in facts.graph.callers(node, 1) if not facts.graph.node(c).get("is_test")]
    failing = [o.nodeid for o in facts.test_run.failed]
    incompatibility = signature_incompatible(unit.old_signature, unit.new_signature)
    if callers and failing:
        return Verdict(claim_id=claim.claim_id, status=V, method="execution", evidence=[f"fail:{t}" for t in failing[:3]],
                       detail=f"signature changed and {len(failing)} test(s) fail on the head")
    if callers and incompatibility:
        return Verdict(claim_id=claim.claim_id, status=V, method="static", evidence=[f"sig:{unit.id}"],
                       detail=f"{incompatibility}; {len(callers)} caller(s)")
    if not unit.signature_changed or not incompatibility:
        return Verdict(claim_id=claim.claim_id, status=R, method="static", evidence=[f"sig:{unit.id}"],
                       detail="signature is backward compatible" + ("" if not unit.signature_changed else f": {unit.old_signature} -> {unit.new_signature}"))
    return Verdict(claim_id=claim.claim_id, status=R, method="static", detail="incompatible signature but no remaining callers")


def _commit_for(store, ref: str) -> dict | None:
    return store.commit(ref.split(":", 1)[1]) if ref.startswith("commit:") else None


def check_history_link(claim: Claim, facts: Facts) -> Verdict:
    store = facts.store
    file, _, qualname = claim.target.partition("::")
    problems, confirmed = [], []
    for ref in claim.evidence_ids:
        if ref.startswith("commit:"):
            commit = _commit_for(store, ref)
            if commit is None:
                problems.append(f"{ref} does not exist")
            elif claim.target in commit["functions"] or any(f["path"] == file for f in commit["files"]):
                confirmed.append(ref)
            else:
                problems.append(f"{ref} does not touch {file}")
        elif ref.startswith(("issue:", "pr:")):
            kind, number = ref.split(":", 1)
            record = store.issue(int(number)) if kind == "issue" else store.pr(int(number))
            if record:
                confirmed.append(ref)
            else:
                problems.append(f"{ref} does not exist")
    if problems:
        return Verdict(claim_id=claim.claim_id, status=R, method="repository", evidence=confirmed, detail="; ".join(problems))
    if not any(r.startswith("commit:") for r in confirmed):
        return Verdict(claim_id=claim.claim_id, status=U, method="repository", evidence=confirmed, detail="no commit cited to tie the link to the code")
    return Verdict(claim_id=claim.claim_id, status=V, method="repository", evidence=confirmed, detail="cited history exists and touches the symbol")


def _cited_text(store, ref: str) -> str | None:
    if ref.startswith("issue:"):
        issue = store.issue(int(ref.split(":")[1]))
        return issue and " ".join([issue["title"], issue["body"]] + [c["body"] for c in issue["comments"]])
    if ref.startswith("pr:"):
        pr = store.pr(int(ref.split(":")[1]))
        return pr and " ".join([pr["title"], pr["body"]] + [r["body"] for r in pr["reviews"]])
    commit = _commit_for(store, ref)
    return commit and commit["message"]


def lexical_support(statement: str, source: str) -> float:
    claim_terms = set(tokenize(statement))
    if not claim_terms:
        return 0.0
    return len(claim_terms & set(tokenize(source))) / len(claim_terms)


def check_rationale(claim: Claim, facts: Facts) -> Verdict:
    texts = {ref: _cited_text(facts.store, ref) for ref in claim.evidence_ids}
    missing = [ref for ref, text in texts.items() if not text]
    if missing:
        return Verdict(claim_id=claim.claim_id, status=R, method="citation", detail=f"cited evidence does not exist: {', '.join(missing)}")
    source = "\n".join(t for t in texts.values() if t)
    statement = claim.assertion if claim.assertion != "affected" else claim.reason
    if facts.llm_allowed and facts.llm is not None:
        try:
            system, user = load_prompt("rationale_judge").render(statement=statement, source=source[:4000])
            judgement, _, _ = facts.llm.structured("rationale_judge", system, user, _Judgement)
            if judgement is not None:
                status = V if judgement.supported else R
                return Verdict(claim_id=claim.claim_id, status=status, method="llm-judge", evidence=list(texts),
                               detail=f"judge quote: {judgement.quote[:160]}" if judgement.quote else "judge found no support")
        except Exception as exc:
            log.info("rationale judge unavailable, using lexical support: %s", exc)
    support = lexical_support(statement, source)
    if support >= ENTAILMENT_SUPPORT:
        status = V
    elif support < ENTAILMENT_REFUTE:
        status = R
    else:
        status = U
    return Verdict(claim_id=claim.claim_id, status=status, method="citation+lexical", evidence=list(texts),
                   detail=f"{support:.0%} of the statement's terms appear in the cited text")


CHECKERS = {
    "test_impact": check_test_impact,
    "module_impact": check_module_impact,
    "api_break": check_api_break,
    "history_link": check_history_link,
    "rationale": check_rationale,
}


def check(claim: Claim, facts: Facts) -> Verdict:
    checker = CHECKERS.get(claim.type)
    if checker is None:
        return Verdict(claim_id=claim.claim_id, status=U, method="none", detail=f"no checker for {claim.type}")
    try:
        return checker(claim, facts)
    except Exception as exc:  # a broken checker must not take the gate down
        log.exception("checker failed for %s", claim.claim_id)
        return Verdict(claim_id=claim.claim_id, status=U, method="error", detail=f"checker error: {exc}")
