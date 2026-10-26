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
from sentinel.verify.equivalence import behaviour_preserving
from sentinel.verify.mutation import generate_mutants, run_mutation
from sentinel.verify.runner import make_workspace, run_tests
from sentinel.verify.sandbox import SandboxSpec

log = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    test_run: TestRun
    line_tests: dict[tuple[str, int], set[str]]
    mutation: MutationReport
    verdicts: list[Verdict]
    import_lines: set[tuple[str, int]] = field(default_factory=set)
    changed_lines_total: int = 0
    changed_lines_covered: int = 0
    uncovered: list[tuple[str, int]] = field(default_factory=list)
    preexisting: set[str] = field(default_factory=set)  # failing tests that already failed on the base

    @property
    def new_failures(self) -> list:
        """Failing tests the change is responsible for (pre-existing failures excluded)."""
        return [o for o in self.test_run.failed if o.nodeid not in self.preexisting]

    @property
    def changed_coverage(self) -> float:
        return self.changed_lines_covered / self.changed_lines_total if self.changed_lines_total else 1.0

    def to_dict(self) -> dict:
        return {
            "tests": {
                "selected": len(self.test_run.selected),
                "passed": len(self.test_run.passed),
                "failed": [o.model_dump() for o in self.test_run.failed],
                "preexisting_failures": sorted(self.preexisting),
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
    def __init__(self, cfg, project_root: Path, sources: dict[str, str], old_sources: dict[str, str | None] | None = None):
        self.cfg = cfg
        self.project_root = project_root
        self.sources = sources
        self.old_sources = old_sources or {}
        self.preserved_units: list[str] = []
        self.emit = lambda *_a, **_k: None  # set by the pipeline: emit(kind, status, message, data)
        self.sandbox = SandboxSpec.from_config(cfg)
        configured = cfg.get("verify.scratch")
        self.scratch = Path(configured) if configured else cfg.workdir / "verify"

    def execute(self, units: list[ChangeUnit], test_ids: list[str]) -> tuple[TestRun, dict, MutationReport, dict[str, set[int]], set]:
        package = self.cfg.get("project.package")
        timeout = float(self.cfg.get("verify.test_timeout_s", 600))
        self.emit("substep", "running", f"running {len(test_ids)} selected test(s) with per-test coverage",
                  {"step": "tests", "tests": len(test_ids), "coverage_package": package, "timeout_s": timeout,
                   "command": ("python -m pytest <selected tests> --cov --cov-context=test in a Docker container (no network, read-only root, "
                               f"{self.sandbox.memory} memory, {self.sandbox.cpus} CPUs, {self.sandbox.pids} processes)" if self.sandbox.docker
                               else "python -m pytest <selected tests> --cov --cov-context=test (host subprocess in the project root, NOT sandboxed)"),
                   "execution": self.sandbox.mode})
        execution = run_tests(
            self.project_root, test_ids, package=package, coverage=True,
            timeout_s=timeout, scratch=self.scratch / "run", sandbox=self.sandbox,
        )
        run = execution.run
        self.emit("substep", "completed" if not run.collection_error else "failed",
                  f"{len(run.passed)} passed, {len(run.failed)} failed in {run.duration_s}s",
                  {"step": "tests", "passed": len(run.passed), "failed": [o.nodeid for o in run.failed][:20],
                   "duration_s": run.duration_s, "collection_error": (run.collection_error or "")[:500] or None})
        changed = changed_source_lines(units, self.sources)
        total_changed = sum(len(v) for v in changed.values())
        covered = {k for k in set(execution.line_tests) | set(execution.import_lines) if k[1] in changed.get(k[0], ())}
        self.emit("substep", "completed", f"{len(covered)} of {total_changed} changed line(s) executed by tests",
                  {"step": "coverage", "changed_lines": total_changed, "covered": len(covered), "files": sorted(changed)})
        # Functions whose edit is alpha-equivalent (renamed locals, docstrings) are not mutated:
        # their surviving mutants would describe the old tests, not this change.
        self.preserved_units = [u.id for u in units if u.kind in ("function", "method") and u.change_type == "modified"
                                and behaviour_preserving(u.qualname, self.old_sources.get(u.file), self.sources.get(u.file))]
        preserved_lines = {(u.file, n) for u in units if u.id in self.preserved_units for n in u.changed_lines}
        mutable = {f: {n for n in lines if (f, n) not in preserved_lines} for f, lines in changed.items()}
        mutable = {f: lines for f, lines in mutable.items() if lines}
        mutation = MutationReport()
        if self.cfg.get("verify.mutation.enabled", True) and mutable and not execution.run.collection_error:
            symbols = {(u.file, n): u.qualname for u in units for n in u.changed_lines}
            mutants = generate_mutants(self.sources, mutable, symbols, limit=int(self.cfg.get("verify.mutation.max_mutants", 24)))
            if mutants:
                self.emit("substep", "running", f"mutating changed lines: {len(mutants)} mutant(s)",
                          {"step": "mutation", "mutants": len(mutants), "files": sorted({m.file for m, _ in mutants})})
                workspace = make_workspace(self.project_root, self.scratch / "workspace")
                broken = {o.nodeid for o in execution.run.failed}
                mutation = run_mutation(
                    workspace, mutants, execution.line_tests, broken_tests=broken,
                    per_mutant_timeout_s=float(self.cfg.get("verify.mutation.per_mutant_timeout_s", 60)),
                    scratch=self.scratch / "mutant",
                    import_lines=execution.import_lines, fallback_tests=test_ids, sandbox=self.sandbox,
                    on_mutant=lambda m: self.emit(
                        "substep", "info", f"mutant {m.status}: {m.file}#L{m.line} {m.original} -> {m.mutated}",
                        {"step": "mutant", "id": m.id, "file": m.file, "line": m.line, "operator": m.operator,
                         "original": m.original, "mutated": m.mutated, "status": m.status, "killed_by": m.killed_by}),
                )
                score = mutation.score
                self.emit("substep", "completed", f"mutation score {score if score is None else round(score, 2)}",
                          {"step": "mutation", "score": score, "killed": sum(m.status == "killed" for m in mutation.mutants),
                           "survived": len(mutation.survived), "duration_s": mutation.duration_s})
            else:
                self.emit("substep", "skipped", "no mutable operators on the changed lines", {"step": "mutation"})
        else:
            self.emit("substep", "skipped", "mutation skipped: disabled, no executable changed lines, or the tests failed to collect",
                      {"step": "mutation"})
        return execution.run, execution.line_tests, mutation, changed, execution.import_lines

    def verify(self, units: list[ChangeUnit], claims: list[Claim], test_ids: list[str], *, graph, store, llm=None, llm_allowed: bool = False) -> VerificationResult:
        run, line_tests, mutation, changed, import_lines = self.execute(units, test_ids)
        facts = Facts(units, run, line_tests, mutation, graph, store, llm, llm_allowed, changed)
        verdicts = [check(c, facts) for c in claims]
        result = self._result(run, line_tests, mutation, verdicts, changed, import_lines)
        base_failing = {t["test_id"] for t in store.all_tests() if t.get("outcome") in ("failed", "error")} if store else set()
        result.preexisting = {o.nodeid for o in run.failed} & base_failing
        return result

    def recheck(self, previous: VerificationResult, units: list[ChangeUnit], claims: list[Claim], *, graph, store, llm=None, llm_allowed=False) -> list[Verdict]:
        """Check new claims against observations already made (used after the impact retry)."""
        changed = changed_source_lines(units, self.sources)
        facts = Facts(units, previous.test_run, previous.line_tests, previous.mutation, graph, store, llm, llm_allowed, changed)
        return [check(c, facts) for c in claims]

    @staticmethod
    def _result(run, line_tests, mutation, verdicts, changed, import_lines=frozenset()) -> VerificationResult:
        total = sum(len(v) for v in changed.values())
        executed = set(line_tests) | set(import_lines)
        covered_keys = {k for k in executed if k[1] in changed.get(k[0], ())}
        uncovered = sorted((f, n) for f, lines in changed.items() for n in lines if (f, n) not in covered_keys)
        return VerificationResult(run, line_tests, mutation, verdicts, set(import_lines), total, len(covered_keys), uncovered)
