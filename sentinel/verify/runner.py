"""Run a subset of the project's tests on the code under review, optionally with coverage."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.data.coverage_map import pytest_env
from sentinel.models import TestOutcome, TestRun

IGNORED = shutil.ignore_patterns(
    ".git", ".sentinel", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "*.egg-info", "htmlcov", ".coverage*", "mutants",
)
EXIT_NO_TESTS = 5


@dataclass
class ExecutionResult:
    run: TestRun
    exit_code: int
    # (file, line) -> test ids that executed it (only when run with coverage)
    line_tests: dict[tuple[str, int], set[str]] = field(default_factory=dict)
    timed_out: bool = False
    stderr: str = ""


def pytest_command(test_ids: list[str], argfile: Path, *, package: str | None, coverage: bool, stop_first: bool, python: str) -> list[str]:
    argfile.write_text("\n".join(test_ids), encoding="utf-8")
    cmd = [python, "-m", "pytest", f"@{argfile}", "-p", "sentinel.verify.pytest_plugin", "-p", "no:cacheprovider", "-q", "-o", "addopts=", "--no-header", "-rN"]
    if coverage and package:
        cmd += [f"--cov={package}", "--cov-context=test", "--cov-report="]
    if stop_first:
        cmd.append("-x")
    return cmd


def run_tests(
    project_root: Path,
    test_ids: list[str],
    *,
    package: str | None = None,
    coverage: bool = False,
    stop_first: bool = False,
    timeout_s: float = 600,
    python: str = sys.executable,
    scratch: Path | None = None,
) -> ExecutionResult:
    if not test_ids:
        return ExecutionResult(TestRun(), 0)
    scratch = Path(scratch or tempfile.mkdtemp(prefix="sentinel-run-"))
    scratch.mkdir(parents=True, exist_ok=True)
    report = (scratch / "report.json").resolve()
    data_file = (scratch / "coverage.dat").resolve()
    for stale in (report, data_file):
        stale.unlink(missing_ok=True)
    cmd = pytest_command(test_ids, (scratch / "tests.args").resolve(), package=package, coverage=coverage, stop_first=stop_first, python=python)
    env = pytest_env({"SENTINEL_TEST_REPORT": str(report), "COVERAGE_FILE": str(data_file)})

    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=project_root, env=env, capture_output=True, text=True, timeout=timeout_s)
        exit_code, stderr, timed_out = proc.returncode, proc.stderr[-3000:] + proc.stdout[-3000:], False
    except subprocess.TimeoutExpired:
        exit_code, stderr, timed_out = -1, "timed out", True
    duration = time.time() - started

    outcomes: list[TestOutcome] = []
    collection_error = None
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        outcomes = [TestOutcome(**r) for r in data.get("results", [])]
        if data.get("collection_errors"):
            collection_error = "\n".join(data["collection_errors"])[:3000]
    elif not timed_out:
        collection_error = stderr[-3000:] or f"pytest exited with {exit_code} and no report"

    line_tests: dict[tuple[str, int], set[str]] = defaultdict(set)
    if coverage and data_file.exists():
        import coverage as coverage_lib

        cov = coverage_lib.CoverageData(basename=str(data_file))
        cov.read()
        root = project_root.resolve()
        for measured in cov.measured_files():
            try:
                rel = Path(measured).resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            for line, contexts in (cov.contexts_by_lineno(measured) or {}).items():
                for context in contexts:
                    if context:
                        line_tests[(rel, line)].add(context.split("|", 1)[0])

    run = TestRun(selected=list(test_ids), outcomes=outcomes, duration_s=round(duration, 3), collection_error=collection_error)
    return ExecutionResult(run, exit_code, dict(line_tests), timed_out, stderr)


def make_workspace(project_root: Path, destination: Path) -> Path:
    """An isolated copy of the project in which files can be mutated safely."""
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(project_root, destination, ignore=IGNORED)
    return destination
