"""Per-test coverage map: which tests execute which lines.

We run the project's suite once with ``coverage`` dynamic contexts (one context per test),
then invert the data into ``(file, line, test_id)`` rows. This map is both an input (test
selection, "changed lines covered" signal) and the ground truth for impact evaluation.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from sentinel.config import PACKAGE_DIR


def project_fingerprint(project_root: Path, package: str, tests_dir: str) -> str:
    """Hash of every Python file in the package and tests: the coverage map's cache key."""
    digest = hashlib.sha256()
    for base in (project_root / package.replace(".", "/"), project_root / tests_dir):
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            digest.update(path.relative_to(project_root).as_posix().encode())
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


def pytest_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    sentinel_root = str(PACKAGE_DIR.parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [sentinel_root, env.get("PYTHONPATH")]))
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.update(extra or {})
    return env


def build_coverage_map(
    project_root: Path,
    package: str,
    tests_dir: str,
    workdir: Path,
    python: str = sys.executable,
    timeout_s: int = 900,
) -> tuple[list[tuple[str, int, str]], list[dict], dict]:
    import coverage

    workdir.mkdir(parents=True, exist_ok=True)
    data_file = (workdir / "coverage-map.dat").resolve()
    report_file = (workdir / "coverage-tests.json").resolve()
    for stale in (data_file, report_file):
        stale.unlink(missing_ok=True)

    cmd = [
        python, "-m", "pytest", tests_dir,
        f"--cov={package}", "--cov-context=test", "--cov-report=",
        "-p", "sentinel.verify.pytest_plugin", "-p", "no:cacheprovider",
        "-q", "-o", "addopts=",
    ]
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=project_root,
        env=pytest_env({"COVERAGE_FILE": str(data_file), "SENTINEL_TEST_REPORT": str(report_file)}),
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    elapsed = time.time() - started
    if not data_file.exists():
        raise RuntimeError(f"coverage run produced no data (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")

    data = coverage.CoverageData(basename=str(data_file))
    data.read()
    rows: list[tuple[str, int, str]] = []
    root = project_root.resolve()
    for measured in data.measured_files():
        try:
            rel = Path(measured).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        for line, contexts in (data.contexts_by_lineno(measured) or {}).items():
            for context in contexts:
                if context:
                    rows.append((rel, line, context.split("|", 1)[0]))

    report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else {"results": []}
    tests = [
        {
            "test_id": r["nodeid"],
            "file": r["nodeid"].split("::", 1)[0],
            "duration": r["duration_s"],
            "outcome": r["outcome"],
        }
        for r in report["results"]
    ]
    summary = {
        "exit_code": proc.returncode,
        "tests": len(tests),
        "failed": sum(1 for t in tests if t["outcome"] in ("failed", "error")),
        "rows": len(rows),
        "elapsed_s": round(elapsed, 2),
    }
    return rows, tests, summary
