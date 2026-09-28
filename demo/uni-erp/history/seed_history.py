"""Rebuild UniERP's development history as a standalone git repository.

The timeline in ``timeline.py`` lists every commit oldest-first. Each step either adds
files or applies edits of the form (path, before, after). Rather than storing every
intermediate file, we start from the *current* source tree and undo the steps newest-first;
that yields the exact content of every file after every step. Replaying those snapshots
forward produces a repository whose HEAD is byte-identical to ``demo/uni-erp``.

Alongside the repository we write ``issues.json`` and ``pulls.json`` — the tracker data a
GitHub-hosted UniERP would have — with pull requests pointing at real commit SHAs.

Usage::

    python demo/uni-erp/history/seed_history.py --out .sentinel/uni-erp-repo
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))

from timeline import ISSUES, STEPS, Step  # noqa: E402

EXCLUDED_DIRS = {".git", "history", "htmlcov", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
START = datetime(2025, 9, 1, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def tracked_files(root: Path) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_dir() or any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if path.suffix in {".pyc", ".sqlite"} or path.name == ".coverage":
            continue
        files[rel.as_posix()] = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return files


def compute_snapshots(final: dict[str, str], steps: list[Step]) -> list[dict[str, str]]:
    """Return the full tree after each step by undoing steps from the final state."""
    state = dict(final)
    snapshots: list[dict[str, str]] = [{}] * len(steps)
    for index in range(len(steps) - 1, -1, -1):
        step = steps[index]
        snapshots[index] = dict(state)
        for edit in reversed(step.edits):
            content = state.get(edit.path)
            if content is None:
                raise SystemExit(f"step {index} ({step.message!r}): {edit.path} does not exist yet")
            occurrences = content.count(edit.after)
            if occurrences != 1:
                raise SystemExit(
                    f"step {index} ({step.message!r}): expected one occurrence of edit text in "
                    f"{edit.path}, found {occurrences}:\n{edit.after[:200]}"
                )
            state[edit.path] = content.replace(edit.after, edit.before, 1)
        for path in step.adds:
            if path not in state:
                raise SystemExit(f"step {index} ({step.message!r}): {path} added twice or missing")
            del state[path]
    if state:
        raise SystemExit(f"files never added by the timeline: {sorted(state)}")
    return snapshots


def _force_remove(func, path, _exc_info) -> None:
    # git marks pack files read-only, which trips rmtree on Windows.
    os.chmod(path, 0o700)
    func(path)


def git(repo: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def commit_dates(count: int) -> list[datetime]:
    rng = random.Random(4011)
    dates, current = [], START
    for _ in range(count):
        current += timedelta(days=rng.randint(1, 8), hours=rng.randint(0, 6), minutes=rng.randint(0, 59))
        # Keep commits inside working hours.
        current = current.replace(hour=9 + rng.randint(0, 9))
        dates.append(current)
    return dates


def write_tree(repo: Path, before: dict[str, str], after: dict[str, str]) -> None:
    for path in before.keys() - after.keys():
        (repo / path).unlink()
    for path, content in after.items():
        if before.get(path) != content:
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")


def seed(out: Path) -> dict:
    final = tracked_files(PROJECT)
    snapshots = compute_snapshots(final, STEPS)

    # Empty the directory rather than deleting it: on Windows a shell sitting inside
    # it would otherwise make the removal fail.
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():
        if child.is_dir():
            shutil.rmtree(child, onerror=_force_remove)
        else:
            child.unlink()
    git(out, "init", "-q", "-b", "main")
    git(out, "config", "core.autocrlf", "false")

    dates = commit_dates(len(STEPS))
    shas: list[str] = []
    previous: dict[str, str] = {}
    for step, snapshot, when in zip(STEPS, snapshots, dates):
        write_tree(out, previous, snapshot)
        previous = snapshot
        name, email = step.author
        stamp = when.isoformat()
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_COMMITTER_DATE": stamp,
        }
        git(out, "add", "-A")
        git(out, "commit", "-q", "--no-verify", "-m", step.full_message(), env=env)
        shas.append(git(out, "rev-parse", "HEAD"))

    if tracked_files(out) != final:
        raise SystemExit("seeded HEAD does not match the current UniERP source tree")

    issues, pulls = tracker_data(shas, dates)
    meta = out.parent / f"{out.name}-meta"
    meta.mkdir(exist_ok=True)
    (meta / "issues.json").write_text(json.dumps(issues, indent=2), encoding="utf-8")
    (meta / "pulls.json").write_text(json.dumps(pulls, indent=2), encoding="utf-8")
    return {"repo": str(out), "meta": str(meta), "commits": len(shas), "issues": len(issues), "pulls": len(pulls)}


def tracker_data(shas: list[str], dates: list[datetime]) -> tuple[list[dict], list[dict]]:
    closing: dict[int, int] = {}
    pulls = []
    for index, step in enumerate(STEPS):
        if step.pr is None:
            continue
        for number in step.closes:
            closing[number] = index
        pulls.append(
            {
                "number": step.pr,
                "title": step.subject,
                "body": step.pr_body or step.body or "",
                "author": step.author[0],
                "state": "merged",
                "created_at": (dates[index] - timedelta(hours=20)).isoformat(),
                "merged_at": dates[index].isoformat(),
                "merge_commit_sha": shas[index],
                "linked_issues": list(step.closes),
                "reviews": [{"author": r[0], "body": r[1]} for r in step.reviews],
            }
        )

    issues = []
    for issue in ISSUES:
        record = {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "labels": list(issue.labels),
            "author": issue.author,
            "comments": [{"author": c[0], "body": c[1]} for c in issue.comments],
            "state": "open",
            "linked_prs": [],
        }
        if issue.number in closing:
            index = closing[issue.number]
            record["state"] = "closed"
            record["created_at"] = (dates[index] - timedelta(days=3)).isoformat()
            record["closed_at"] = dates[index].isoformat()
            record["linked_prs"] = [STEPS[index].pr]
        else:
            anchor = dates[min(issue.opened_after, len(dates) - 1)]
            record["created_at"] = (anchor + timedelta(hours=4)).isoformat()
            if issue.closed:
                record["state"] = "closed"
                record["closed_at"] = (anchor + timedelta(days=2)).isoformat()
        issues.append(record)
    return issues, pulls


def check_history(repo: Path) -> list[tuple[str, str]]:
    """Run the test suite at every commit that has tests; return (sha, subject) of red ones.

    A plausible history has green CI throughout: a test that fails on an old commit would have
    been noticed then, and would make old revisions useless as benchmark bases.
    """
    red = []
    shas = git(repo, "rev-list", "--reverse", "HEAD").split()
    tree = repo.parent / f"{repo.name}-check"
    if tree.exists():
        git(repo, "worktree", "remove", "--force", str(tree))
    git(repo, "worktree", "add", "--detach", str(tree), shas[0])
    try:
        for sha in shas:
            git(tree, "checkout", "-q", "--force", sha)
            if not (tree / "tests").exists() or not any((tree / "tests").glob("test_*.py")):
                continue
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=tree,
                                    capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            if result.returncode not in (0, 5):
                red.append((sha[:10], git(tree, "log", "-1", "--format=%s")))
    finally:
        git(repo, "worktree", "remove", "--force", str(tree))
    return red


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(".sentinel/uni-erp-repo"))
    parser.add_argument("--check", action="store_true", help="also run the tests at every commit")
    args = parser.parse_args()
    summary = seed(args.out.resolve())
    if args.check:
        summary["red_commits"] = check_history(args.out.resolve())
    print(json.dumps(summary, indent=2))
    if summary.get("red_commits"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
