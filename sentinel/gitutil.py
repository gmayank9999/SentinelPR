"""Thin wrappers around the git CLI.

We shell out to git rather than binding a library: git is always present on CI runners,
its output formats are stable, and it handles every repository layout correctly.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(repo: Path | str, *args: str, check: bool = True, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=input_text,
    )
    if check and result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def rev_parse(repo: Path | str, rev: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").strip()


def merge_base(repo: Path | str, a: str, b: str) -> str:
    return git(repo, "merge-base", a, b).strip()


def show_file(repo: Path | str, rev: str, path: str) -> str | None:
    """File content at ``rev``, or None when the file does not exist there."""
    result = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout if result.returncode == 0 else None


def unified_diff(repo: Path | str, base: str, head: str, paths: list[str] | None = None) -> str:
    args = ["diff", "--no-color", "--no-ext-diff", "-M", "--unified=0", f"{base}...{head}"]
    if paths:
        args += ["--", *paths]
    return git(repo, *args)


def diff_between(repo: Path | str, base: str, head: str, paths: list[str] | None = None) -> str:
    """Two-dot diff (exact trees), used when ``base`` is already the merge base."""
    args = ["diff", "--no-color", "--no-ext-diff", "-M", "--unified=0", base, head]
    if paths:
        args += ["--", *paths]
    return git(repo, *args)


def list_files(repo: Path | str, rev: str = "HEAD", prefix: str = "") -> list[str]:
    out = git(repo, "ls-tree", "-r", "--name-only", rev, *([prefix] if prefix else []))
    return [line for line in out.splitlines() if line]


@dataclass
class BlameLine:
    sha: str
    line: int
    author: str
    author_time: int


def blame(repo: Path | str, rev: str, path: str, lines: list[int] | None = None) -> dict[int, BlameLine]:
    """Map final line number -> blame info at ``rev``."""
    args = ["blame", "--porcelain", "-w"]
    for start, end in _ranges(lines or []):
        args += ["-L", f"{start},{end}"]
    args += [rev, "--", path]
    out = git(repo, *args, check=False)
    result: dict[int, BlameLine] = {}
    commits: dict[str, dict[str, str]] = {}
    current_sha, current_line = "", 0
    for raw in out.splitlines():
        if raw.startswith("\t"):
            info = commits.get(current_sha, {})
            result[current_line] = BlameLine(
                current_sha, current_line, info.get("author", ""), int(info.get("author-time", "0") or 0)
            )
            continue
        parts = raw.split(" ")
        if len(parts) >= 3 and len(parts[0]) == 40 and all(c in "0123456789abcdef" for c in parts[0]):
            current_sha, current_line = parts[0], int(parts[2])
            commits.setdefault(current_sha, {})
        elif current_sha and parts[0] in ("author", "author-time"):
            commits[current_sha][parts[0]] = " ".join(parts[1:])
    return result


def _ranges(lines: list[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for n in sorted(set(lines)):
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], n)
        else:
            ranges.append((n, n))
    return ranges


def is_repo(path: Path | str) -> bool:
    return (
        subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=str(path), capture_output=True, text=True
        ).returncode
        == 0
    )
