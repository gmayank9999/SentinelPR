"""Execution oracle: ground-truth labels for generated PRs.

A PR is **defective** when some test that passes on the base revision fails on the PR, where
"some test" means the base revision's own tests *or* the hidden oracle tests. Using the
base's tests (not the PR's) means a PR cannot hide a regression by editing or deleting the
test that would catch it.

The oracle also runs the PR's visible suite with per-test coverage, giving the ground truth
for change-impact prediction: the tests that execute a changed line (plus those that fail).
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.verify.runner import run_tests

ORACLE_DIR = ".oracle"


@dataclass
class OracleResult:
    label: int
    newly_failing: list[str]
    visible_failing: list[str]
    visible_collection_error: str | None
    impacted_tests: list[str]
    impacted_modules: list[str]
    changed_lines: int
    duration_s: float
    visible_tests: int = 0
    notes: list[str] = field(default_factory=list)


class Oracle:
    def __init__(self, base_tests: dict[str, str], hidden_dir: Path | None, package: str, tests_dir: str = "tests"):
        self.base_tests = base_tests  # project-relative path -> content at the base revision
        self.hidden_dir = hidden_dir
        self.package = package
        self.tests_dir = tests_dir
        self.base_passing: set[str] | None = None
        self.base_visible_failing: set[str] = set()

    def _materialise(self, tree: Path) -> list[str]:
        oracle = tree / ORACLE_DIR
        if oracle.exists():
            shutil.rmtree(oracle)
        for rel, content in self.base_tests.items():
            target = oracle / "base" / Path(rel).relative_to(self.tests_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        targets = [f"{ORACLE_DIR}/base"]
        if self.hidden_dir and self.hidden_dir.exists():
            shutil.copytree(self.hidden_dir, oracle / "hidden")
            targets.append(f"{ORACLE_DIR}/hidden")
        return targets

    def _oracle_run(self, tree: Path, scratch: Path) -> tuple[set[str], set[str], str | None]:
        targets = self._materialise(tree)
        try:
            result = run_tests(tree, targets, timeout_s=600, scratch=scratch, extra_args=["--import-mode=importlib"])
        finally:
            shutil.rmtree(tree / ORACLE_DIR, ignore_errors=True)
        passed = {o.nodeid for o in result.run.outcomes if o.outcome == "passed"}
        failed = {o.nodeid for o in result.run.outcomes if o.outcome in ("failed", "error")}
        return passed, failed, result.run.collection_error

    def baseline(self, tree: Path, scratch: Path) -> set[str]:
        passed, _, _ = self._oracle_run(tree, scratch)
        self.base_passing = passed
        # Tests that already fail on the base are not the PR's doing.
        visible = run_tests(tree, [self.tests_dir], timeout_s=600, scratch=scratch / "visible")
        self.base_visible_failing = {o.nodeid for o in visible.run.failed}
        return passed

    def evaluate(self, tree: Path, changed: dict[str, set[int]], scratch: Path) -> OracleResult:
        assert self.base_passing is not None, "call baseline() on the unmodified base first"
        started = time.time()
        passed, _, collection_error = self._oracle_run(tree, scratch / "oracle")
        # Anything that passed on the base and does not pass now (failed, errored, or no longer
        # collected because an import broke) is a regression.
        newly_failing = sorted(self.base_passing - passed)

        visible = run_tests(tree, [self.tests_dir], package=self.package, coverage=True, timeout_s=600, scratch=scratch / "visible")
        visible_failing = sorted({o.nodeid for o in visible.run.failed} - self.base_visible_failing)
        impacted: set[str] = set(visible_failing)
        modules: set[str] = set()
        executed_by: dict[str, set[str]] = {}
        for (path, line), tests in visible.line_tests.items():
            for t in tests:
                executed_by.setdefault(t, set()).add(path)
            if line in changed.get(path, ()):
                impacted |= tests
        for test in impacted:
            modules |= {p for p in executed_by.get(test, ()) if p.startswith(self.package.replace(".", "/") + "/")}
        modules -= set(changed)
        notes = []
        if collection_error:
            notes.append("oracle collection error: " + collection_error[:200])
        return OracleResult(
            label=int(bool(newly_failing) or bool(visible.run.collection_error)),
            newly_failing=newly_failing,
            visible_failing=visible_failing,
            visible_collection_error=visible.run.collection_error,
            impacted_tests=sorted(impacted),
            impacted_modules=sorted(modules),
            changed_lines=sum(len(v) for v in changed.values()),
            duration_s=round(time.time() - started, 2),
            visible_tests=len(visible.run.outcomes),
            notes=notes,
        )
