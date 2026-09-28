"""Patch Advisor: turn a problem report into a small, tested, self-gated fix.

1. Retrieve the project code relevant to the problem (hybrid retrieval, with the functions
   an issue or a failing PR points at as graph seeds).
2. Ask the LLM for exact find/replace edits plus a regression test (prompt ``patch``).
3. Apply the edits to a scratch copy; reject anything that does not apply cleanly.
4. Static safety review (no new dependencies, no CI/build edits, no eval/shell=True ...).
5. Run the affected tests and the new test in the scratch copy.
Only a patch that passes all of this is offered; ``sentinel.commands`` then opens it as a
draft PR, which SentinelPR analyses like any other PR before a human decides.

Without an LLM there is no patch; ``remediation_plan`` instead lists the concrete gaps
(uncovered lines, surviving mutants) a developer should close.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from sentinel.agents.prompts import load_prompt
from sentinel.guards.input import neutralise, redact_secrets
from sentinel.guards.output import PatchReview, review_patch
from sentinel.models import TestRun
from sentinel.retrieval.graph import is_test_path
from sentinel.verify.runner import make_workspace, run_tests

log = logging.getLogger(__name__)
MAX_CONTEXT_CHARS = 14000


class _Edit(BaseModel):
    path: str
    find: str
    replace: str


class _NewFile(BaseModel):
    path: str
    content: str


class _Plan(BaseModel):
    summary: str
    edits: list[_Edit] = Field(default_factory=list)
    new_files: list[_NewFile] = Field(default_factory=list)


@dataclass
class PatchProposal:
    summary: str
    files: dict[str, str]  # project-relative path -> full new content
    originals: dict[str, str | None]
    review: PatchReview | None = None
    tests: TestRun | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        tests_ok = self.tests is not None and not self.tests.failed and not self.tests.collection_error and bool(self.tests.outcomes)
        return not self.problems and self.review is not None and self.review.allowed and tests_ok


def apply_plan(plan: _Plan, sources: dict[str, str]) -> tuple[dict[str, str], dict[str, str | None], list[str]]:
    files: dict[str, str] = {}
    originals: dict[str, str | None] = {}
    problems: list[str] = []
    for edit in plan.edits:
        current = files.get(edit.path, sources.get(edit.path))
        if current is None:
            problems.append(f"{edit.path}: file does not exist")
            continue
        count = current.count(edit.find) if edit.find else 0
        if count != 1:
            problems.append(f"{edit.path}: snippet to replace found {count} times (must be exactly once)")
            continue
        originals.setdefault(edit.path, sources.get(edit.path))
        files[edit.path] = current.replace(edit.find, edit.replace, 1)
    for new in plan.new_files:
        if new.path in sources:
            problems.append(f"{new.path}: already exists; use an edit instead")
            continue
        originals[new.path] = None
        files[new.path] = new.content if new.content.endswith("\n") else new.content + "\n"
    return files, originals, problems


class PatchAdvisor:
    def __init__(self, cfg, retriever, llm, sources: dict[str, str]):
        self.cfg, self.retriever, self.llm, self.sources = cfg, retriever, llm, sources

    def context(self, problem: str, seeds: list[str]) -> str:
        results = self.retriever.search_code(problem, seeds=seeds, top_k=8)
        blocks, seen, size = [], set(), 0
        for r in results:
            if r.path in seen or r.path is None:
                continue
            seen.add(r.path)
            text = self.sources.get(r.path, r.text)
            block = f"### {r.path}\n```python\n{text}\n```"
            if size + len(block) > MAX_CONTEXT_CHARS:
                block = f"### {r.path} (excerpt)\n```python\n{r.text}\n```"
            blocks.append(block)
            size += len(block)
            if size > MAX_CONTEXT_CHARS:
                break
        return "\n\n".join(blocks)

    def propose(self, problem: str, seeds: list[str] | None = None) -> PatchProposal | None:
        if self.llm is None or not self.llm.available:
            return None
        problem = redact_secrets(neutralise(problem))[:3000]
        system, user = load_prompt("patch").render(problem=problem, context=self.context(problem, seeds or []))
        plan, _, errors = self.llm.structured("patch", system, user, _Plan, max_tokens=2500)
        if plan is None:
            log.warning("patch plan could not be parsed: %s", errors)
            return None
        files, originals, problems = apply_plan(plan, self.sources)
        proposal = PatchProposal(plan.summary, files, originals, problems=problems)
        if not files:
            proposal.problems.append("the plan changed nothing")
        return proposal

    def validate(self, proposal: PatchProposal, test_ids: list[str], scratch: Path) -> PatchProposal:
        package = self.cfg.get("project.package")
        project_modules = {package.split(".")[0], "tests", "conftest"}
        proposal.review = review_patch(proposal.files, proposal.originals, project_modules)
        if proposal.problems or not proposal.review.allowed:
            return proposal
        workspace = make_workspace(self.cfg.project_root, scratch / "patch-workspace")
        for path, content in proposal.files.items():
            target = workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        new_tests = [p for p in proposal.files if is_test_path(p) and proposal.originals.get(p) is None]
        targets = sorted(set(test_ids) | set(new_tests) | {p for p in proposal.files if is_test_path(p)})
        proposal.tests = run_tests(workspace, targets or [self.cfg.get("project.tests", "tests")], scratch=scratch / "patch-run").run
        return proposal


def remediation_plan(report: dict) -> str:
    """What to fix, from the evidence alone (used when no LLM is available)."""
    verification = report.get("verification") or {}
    lines = ["**Patch Advisor needs an LLM provider to write code; here is what the evidence says to fix:**", ""]
    for failure in (verification.get("tests") or {}).get("failed", [])[:5]:
        lines.append(f"- Failing test `{failure['nodeid']}`: {failure.get('message', '').strip().splitlines()[-1][:160] if failure.get('message') else ''}")
    for ref in (verification.get("changed_lines") or {}).get("uncovered", [])[:8]:
        lines.append(f"- Add a test that executes `{ref}` (no test runs it).")
    for m in (verification.get("mutation") or {}).get("mutants", []):
        if m["status"] == "survived":
            lines.append(f"- Strengthen assertions around `{m['file']}#L{m['line']}`: changing `{m['original']}` to `{m['mutated']}` goes unnoticed.")
    if len(lines) == 2:
        lines.append("- Nothing concrete: tests pass, changed lines are executed and every mutant is killed.")
    return "\n".join(lines)
