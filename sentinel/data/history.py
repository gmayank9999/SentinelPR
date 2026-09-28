"""Commit history mining.

For every commit we record file-level diff stats *and* the functions it touched, by mapping
its diff onto the structure of the file before and after. Function-level history is what
lets the Historian say "compute_gpa was changed by three bug-fix commits".
"""

from __future__ import annotations

import re
from pathlib import Path

from sentinel import gitutil
from sentinel.retrieval.diff_parser import change_units_for_file, parse_unified_diff

RECORD = "\x1e"
FIELD = "\x1f"

ISSUE_REF = re.compile(r"(?:(?<=\s)|(?<=\()|^)#(\d+)\b")
CLOSING_REF = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.IGNORECASE)
FIX_WORDS = re.compile(r"\b(fix(?:e[sd]|ing)?|bug(?:fix)?|defect|regression|hotfix|patch(?:ed)?)\b", re.IGNORECASE)


def referenced_issues(message: str) -> list[int]:
    return sorted({int(n) for n in ISSUE_REF.findall(message)})


def closing_issues(message: str) -> list[int]:
    return sorted({int(n) for n in CLOSING_REF.findall(message)})


def classify_fix(message: str, issue_labels: dict[int, list[str]] | None = None) -> tuple[bool, list[int]]:
    """Decide whether a commit fixes a bug.

    With tracker data we trust labels: a commit is a fix when it closes an issue labelled as a
    bug. Without tracker data we fall back to the usual keyword heuristic.
    """
    closes = closing_issues(message)
    if issue_labels:
        bug_issues = [n for n in closes if any("bug" in label.lower() for label in issue_labels.get(n, []))]
        if bug_issues:
            return True, bug_issues
        if closes and all(n in issue_labels for n in closes):
            return False, []  # closes known non-bug issues (features, questions)
    subject = message.strip().splitlines()[0] if message.strip() else ""
    return bool(FIX_WORDS.search(subject)), closes


def _parse_log(raw: str) -> list[dict]:
    commits = []
    for record in raw.split(RECORD):
        record = record.strip("\n")
        if not record:
            continue
        header, _, numstat = record.partition(FIELD + "END" + FIELD)
        sha, parents, author, email, timestamp, message = header.split(FIELD, 5)
        files = []
        for line in numstat.strip().splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            adds, dels, path = parts
            if " => " in path:  # rename: "dir/{old => new}.py" or "old => new"
                path = _rename_target(path)
            files.append(
                {
                    "path": path,
                    "additions": int(adds) if adds.isdigit() else 0,
                    "deletions": int(dels) if dels.isdigit() else 0,
                }
            )
        commits.append(
            {
                "sha": sha,
                "parents": parents.split(),
                "author": author,
                "email": email,
                "timestamp": int(timestamp),
                "message": message.strip(),
                "files": files,
            }
        )
    return commits


def _rename_target(path: str) -> str:
    match = re.match(r"(.*)\{(.*) => (.*)\}(.*)", path)
    if match:
        prefix, _, new, suffix = match.groups()
        return (prefix + new + suffix).replace("//", "/")
    return path.split(" => ")[-1]


def mine_commits(
    repo: Path,
    prefix: str = "",
    max_commits: int | None = None,
    with_functions: bool = True,
    issue_labels: dict[int, list[str]] | None = None,
) -> list[dict]:
    """All non-merge commits touching ``prefix`` (oldest first), paths relative to ``prefix``."""
    fmt = FIELD.join(["%H", "%P", "%an", "%ae", "%at", "%B"]) + FIELD + "END" + FIELD
    args = ["log", "--no-merges", "--reverse", "-M", "--numstat", f"--format={RECORD}{fmt}"]
    if max_commits:
        args.insert(1, f"--max-count={max_commits}")
    args += ["--", prefix.rstrip("/")] if prefix else []
    commits = _parse_log(gitutil.git(repo, *args))

    for commit in commits:
        for f in commit["files"]:
            if prefix and f["path"].startswith(prefix):
                f["path"] = f["path"][len(prefix) :]
        commit["files"] = [f for f in commit["files"] if not (prefix and f["path"].startswith("../"))]
        commit["additions"] = sum(f["additions"] for f in commit["files"])
        commit["deletions"] = sum(f["deletions"] for f in commit["files"])
        commit["is_fix"], commit["issues"] = classify_fix(commit["message"], issue_labels)
        commit["issues"] = commit["issues"] or referenced_issues(commit["message"])
        commit["functions"] = touched_functions(repo, commit, prefix) if with_functions else []
    return commits


def touched_functions(repo: Path, commit: dict, prefix: str = "") -> list[str]:
    py_files = [f["path"] for f in commit["files"] if f["path"].endswith(".py")]
    if not py_files:
        return []
    sha = commit["sha"]
    parent = commit["parents"][0] if commit["parents"] else None
    raw = gitutil.git(
        repo, "show", "--no-color", "--unified=0", "-M", "--format=", sha, "--", *[prefix + p for p in py_files]
    )
    units: list[str] = []
    for change in parse_unified_diff(raw):
        path = change.path[len(prefix) :] if prefix and change.path.startswith(prefix) else change.path
        old_path = change.old_path
        old = gitutil.show_file(repo, parent, old_path) if parent and old_path and change.status != "added" else None
        new = gitutil.show_file(repo, sha, change.path) if change.status != "removed" else None
        change.path = path
        for unit in change_units_for_file(change, old, new):
            if unit.kind != "module":
                units.append(unit.id)
    return sorted(set(units))
