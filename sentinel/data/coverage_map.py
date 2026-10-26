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


def package_dir(project_root: Path, package: str) -> str:
    """Project-relative directory of the import package: ``pkg``, or ``src/pkg`` in a src layout."""
    rel = package.replace(".", "/")
    if not (project_root / rel).is_dir() and (project_root / "src" / rel).is_dir():
        return "src/" + rel
    return rel


def source_roots(project_root: Path) -> list[Path]:
    """Directories to put on the import path so tests exercise the checked-out code (src layout)
    rather than a copy installed in the environment."""
    src = project_root / "src"
    if src.is_dir() and not (src / "__init__.py").exists():
        return [src.resolve()]
    return []


def project_fingerprint(project_root: Path, package: str, tests_dir: str) -> str:
    """Hash of every Python file in the package and tests: the coverage map's cache key."""
    digest = hashlib.sha256()
    for base in (project_root / package_dir(project_root, package), project_root / tests_dir):
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            digest.update(path.relative_to(project_root).as_posix().encode())
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


PYTEST_CONFIG_FILES = ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")


def pytest_isolation(project_root: Path, scratch: Path) -> list[str]:
    """Pin pytest's rootdir to the project so node ids are stable, and stop a config file in a
    parent directory from leaking into the project's test run."""
    args = [f"--rootdir={project_root.resolve()}"]
    if not any((project_root / name).exists() for name in PYTEST_CONFIG_FILES):
        scratch.mkdir(parents=True, exist_ok=True)
        empty = scratch / "pytest.ini"
        empty.write_text("[pytest]\n", encoding="utf-8")
        args += ["-c", str(empty.resolve())]
    return args


def pytest_env(extra: dict | None = None, project_root: Path | None = None) -> dict:
    env = dict(os.environ)
    sentinel_root = str(PACKAGE_DIR.parent)
    sources = [str(p) for p in source_roots(project_root)] if project_root else []
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [*sources, sentinel_root, env.get("PYTHONPATH")]))
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
    sandbox=None,
) -> tuple[list[tuple[str, int, str]], list[dict], dict]:
    import coverage

    workdir.mkdir(parents=True, exist_ok=True)
    data_file = (workdir / "coverage-map.dat").resolve()
    report_file = (workdir / "coverage-tests.json").resolve()
    for stale in (data_file, report_file):
        stale.unlink(missing_ok=True)

    in_container = sandbox is not None and sandbox.docker
    cmd = [
        python, "-m", "pytest", tests_dir,
        f"--cov={package}", "--cov-context=test", "--cov-report=",
        "-p", "sentinel.verify.pytest_plugin", "-p", "no:cacheprovider",
        "-q", "-o", "addopts=", *pytest_isolation(project_root, workdir),
    ]
    started = time.time()
    if in_container:
        from sentinel.verify import sandbox as sbx

        result = sbx.run_pytest(sandbox, project_dir=project_root, scratch=workdir / "cov-scratch", pytest_args=[tests_dir], package=package, coverage=True, timeout_s=timeout_s)
        data_file, report_file = workdir / "cov-scratch" / "coverage.dat", workdir / "cov-scratch" / "report.json"
        proc = subprocess.CompletedProcess([], result.returncode, result.stdout, result.stderr)
    else:
        proc = subprocess.run(
            cmd,
            cwd=project_root,
            env=pytest_env({"COVERAGE_FILE": str(data_file), "SENTINEL_TEST_REPORT": str(report_file)}, project_root),
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
        if in_container:
            if not measured.startswith("/work/"):
                continue
            rel = measured[len("/work/"):]
        else:
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
