"""SZZ: trace bug-fixing commits back to the commits that introduced the bug.

For each fix we look at the lines it deleted or modified in non-test source files and ask
``git blame`` (on the fix's parent, ignoring whitespace) who last touched them. Those
commits are the likely bug introducers.

Two refinements over the textbook algorithm:

* Lines that are blank or comments carry no behaviour and are ignored.
* Pure additions (the fix only inserted lines, e.g. a missing guard clause) would give
  classic SZZ nothing to blame. For those we blame the non-blank lines immediately around
  the insertion inside the same function, with lower confidence.

When the fixed issue has a creation date, introducers committed *after* the report are
discarded: they cannot have caused a bug that already existed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sentinel import gitutil
from sentinel.retrieval.chunker import parse_python
from sentinel.retrieval.diff_parser import parse_unified_diff

ADJACENT_CONFIDENCE = 0.5


def _is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") or "/tests/" in path or name.startswith("test_") or name.endswith("_test.py")


def _meaningful(lines: list[str], n: int) -> bool:
    if not 0 < n <= len(lines):
        return False
    text = lines[n - 1].strip()
    return bool(text) and not text.startswith("#")


def _issue_timestamp(issue: dict | None) -> float | None:
    if not issue or not issue.get("created_at"):
        return None
    try:
        return datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def szz_for_fix(repo: Path, fix: dict, prefix: str = "", issues: dict[int, dict] | None = None) -> list[dict]:
    if not fix["parents"]:
        return []
    parent = fix["parents"][0]
    source_files = [f["path"] for f in fix["files"] if f["path"].endswith(".py") and not _is_test_path(f["path"])]
    if not source_files:
        return []
    raw = gitutil.git(repo, "show", "--no-color", "--unified=0", "-M", "--format=", fix["sha"], "--", *[prefix + p for p in source_files])
    issue_number = fix["issues"][0] if fix.get("issues") else None
    cutoff = _issue_timestamp((issues or {}).get(issue_number)) if issue_number else None

    links: list[dict] = []
    for change in parse_unified_diff(raw):
        if change.status == "added":
            continue
        old_repo_path = change.old_path or change.path
        old_source = gitutil.show_file(repo, parent, old_repo_path)
        if old_source is None:
            continue
        rel = old_repo_path[len(prefix) :] if prefix and old_repo_path.startswith(prefix) else old_repo_path
        old_lines = old_source.splitlines()
        info = parse_python(old_source, rel)

        targets: dict[int, float] = {n: 1.0 for n in change.removed_lines if _meaningful(old_lines, n)}
        for anchor in change.insertion_points:
            # Lines were inserted after old line `anchor`: blame its neighbours in the same function.
            symbol = info.innermost(max(anchor, 1))
            for n in (anchor, anchor + 1):
                if _meaningful(old_lines, n) and (symbol is None or symbol.contains(n)):
                    targets.setdefault(n, ADJACENT_CONFIDENCE)
        if not targets:
            continue

        blamed = gitutil.blame(repo, parent, old_repo_path, list(targets))
        for line, info_line in blamed.items():
            if info_line.sha.startswith("0000000"):
                continue
            if cutoff is not None and info_line.author_time > cutoff:
                continue
            symbol = info.innermost(line)
            links.append(
                {
                    "fix_sha": fix["sha"],
                    "introducing_sha": info_line.sha,
                    "file": rel,
                    "function": symbol.qualname if symbol else None,
                    "line": line,
                    "confidence": targets.get(line, 1.0),
                    "issue": issue_number,
                }
            )
    return links


def run_szz(repo: Path, commits: list[dict], prefix: str = "", issues: dict[int, dict] | None = None) -> list[dict]:
    links: list[dict] = []
    known = {c["sha"] for c in commits}
    for commit in commits:
        if not commit.get("is_fix"):
            continue
        for link in szz_for_fix(repo, commit, prefix, issues):
            # The fix cannot introduce its own bug, and introducers must be in the mined range.
            if link["introducing_sha"] != commit["sha"] and link["introducing_sha"] in known:
                links.append(link)
    return _dedupe(links)


def _dedupe(links: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for link in links:
        key = (link["fix_sha"], link["introducing_sha"], link["file"], link["function"])
        if key not in seen or link["confidence"] > seen[key]["confidence"]:
            seen[key] = link
    return list(seen.values())


def defect_summary(links: list[dict]) -> dict[str, dict]:
    """Per change-unit id ("file::function"): distinct fixes and introducers."""
    summary: dict[str, dict] = {}
    for link in links:
        if not link.get("function"):
            continue
        unit = f"{link['file']}::{link['function']}"
        entry = summary.setdefault(unit, {"fixes": set(), "introducers": set(), "issues": set()})
        entry["fixes"].add(link["fix_sha"])
        entry["introducers"].add(link["introducing_sha"])
        if link.get("issue"):
            entry["issues"].add(link["issue"])
    return summary
