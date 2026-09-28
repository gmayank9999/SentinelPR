"""Verifier: execute the head revision and check every claim against what actually happens.

Steps
    1. Run the selected tests on the head with per-test coverage contexts.
    2. Measure how many changed lines those tests execute.
    3. Mutate changed lines (in an isolated copy) and run the covering tests against each mutant.
    4. Check each claim with the matching checker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.models import ChangeUnit, Claim, MutationReport, TestRun, Verdict
from sentinel.retrieval.graph import is_test_path
from sentinel.verify.checkers import Facts, check
from sentinel.verify.mutation import generate_mutants, run_mutation
from sentinel.verify.runner import make_workspace, run_tests

log = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    test_run: TestRun
    line_tests: dict[tuple[str, int], set[str]]
    mutation: MutationReport
    verdicts: list[Verdict]
    changed_lines_total: int = 0
    changed_lines_covered: int = 0
    uncovered: list[tuple[str, int]] = field(default_factory=list)

    @property
    def changed_coverage(self) -> float:
        return self.changed_lines_covered / self.changed_lines_total if self.changed_lines_total else 1.0

    def to_dict(self) -> dict:
        return {
            "tests": {
                "selected": len(self.test_run.selected),
                "passed": len(self.test_run.passed),
                "failed": [o.model_dump() for o in self.test_run.failed],
                "duration_s": self.test_run.duration_s,
                "collection_error": self.test_run.collection_error,
            },
            "changed_lines": {
                "total": self.changed_lines_total,
                "covered": self.changed_lines_covered,
                "coverage": round(self.changed_coverage, 4),
                "uncovered": [f"{f}#L{n}" for f, n in self.uncovered[:50]],
            },
            "mutation": {
                "score": self.mutation.score,
                "mutants": [m.model_dump() for m in self.mutation.mutants],
                "duration_s": self.mutation.duration_s,
            },
        }


def changed_source_lines(units: list[ChangeUnit], sources: dict[str, str]) -> dict[str, set[int]]:
    """Executable changed lines in application code (blank lines and comments excluded)."""
    result: dict[str, set[int]] = {}
    for unit in units:
        if is_test_path(unit.file) or not unit.changed_lines:
            continue
        text = sources.get(unit.file, "").splitlines()
        for n in unit.changed_lines:
            if 0 < n <= len(text):
                stripped = text[n - 1].strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith(('"""', "'''")):
                    result.setdefault(unit.file, set()).add(n)
    return result


class Verifier:
    def __init__(self, cfg, project_root: Path, sources: dict[str, str]):
        self.cfg = cfg
        self.project_root = project_root
        self.sources = sources
        self.scratch = cfg.workdir / "verify"

    def execute(self, units: list[ChangeUnit], test_ids: list[str]) -> tuple[TestRun, dict, MutationReport, dict[str, set[int]]]:
        package = self.cfg.get("project.package")
        execution = run_tests(
            self.project_root, test_ids, package=package, coverage=True,
            timeout_s=float(self.cfg.get("verify.test_timeout_s", 600)), scratch=self.scratch / "run",
        )
        changed = changed_source_lines(units, self.sources)
        mutation = MutationReport()
        if self.cfg.get("verify.mutation.enabled", True) and changed and not execution.run.collection_error:
            symbols = {(u.file, n): u.qualname for u in units for n in u.changed_lines}
            mutants = generate_mutants(self.sources, changed, symbols, limit=int(self.cfg.get("verify.mutation.max_mutants", 24)))
            if mutants:
                workspace = make_workspace(self.project_root, self.scratch / "workspace")
                broken = {o.nodeid for o in execution.run.failed}
                mutation = run_mutation(
                    workspace, mutants, execution.line_tests, broken_tests=broken,
                    per_mutant_timeout_s=float(self.cfg.get("verify.mutation.per_mutant_timeout_s", 60)),
                    scratch=self.scratch / "mutant",
                )
        return execution.run, execution.line_tests, mutation, changed

    def verify(self, units: list[ChangeUnit], claims: list[Claim], test_ids: list[str], *, graph, store, llm=None, llm_allowed: bool = False) -> VerificationResult:
        run, line_tests, mutation, changed = self.execute(units, test_ids)
        facts = Facts(units, run, line_tests, mutation, graph, store, llm, llm_allowed, changed)
        verdicts = [check(c, facts) for c in claims]
        return self._result(run, line_tests, mutation, verdicts, changed)

    def recheck(self, previous: VerificationResult, units: list[ChangeUnit], claims: list[Claim], *, graph, store, llm=None, llm_allowed=False) -> list[Verdict]:
        """Check new claims against observations already made (used after the impact retry)."""
        changed = changed_source_lines(units, self.sources)
        facts = Facts(units, previous.test_run, previous.line_tests, previous.mutation, graph, store, llm, llm_allowed, changed)
        return [check(c, facts) for c in claims]

    @staticmethod
    def _result(run, line_tests, mutation, verdicts, changed) -> VerificationResult:
        total = sum(len(v) for v in changed.values())
        covered_keys = {k for k in line_tests if k[1] in changed.get(k[0], ())}
        uncovered = sorted((f, n) for f, lines in changed.items() for n in lines if (f, n) not in covered_keys)
        return VerificationResult(run, line_tests, mutation, verdicts, total, len(covered_keys), uncovered)
