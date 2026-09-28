"""C4: historical regressions — undo a real bug fix (identified from the history) on the base.

Two variants per fix:
    source-only   the fix's source changes are reverted; its regression test stays
    full revert   the regression test is reverted too ("revert the revert"), so the visible
                  suite may well pass — only the hidden oracle and history can tell
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from bench.generate.base import PRSpec, Snapshot, compiles
from sentinel import gitutil
from sentinel.data.history import mine_commits
from sentinel.retrieval.graph import is_test_path


def _reverse_apply(snapshot: Snapshot, fix_sha: str, paths: list[str]) -> dict[str, str] | None:
    """Apply the inverse of ``fix_sha``'s diff (restricted to ``paths``) onto the base files."""
    diff = gitutil.git(snapshot.repo, "diff", "--no-color", f"{fix_sha}^", fix_sha, "--", *[snapshot.prefix + p for p in paths])
    if not diff.strip():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for p in paths:
            content = snapshot.read(p)
            if content is None:
                return None
            target = root / (snapshot.prefix + p)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        patch = root / "fix.patch"
        patch.write_text(diff, encoding="utf-8", newline="\n")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        result = subprocess.run(["git", "apply", "-R", "--whitespace=nowarn", str(patch)], cwd=root, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        edits = {p: (root / (snapshot.prefix + p)).read_text(encoding="utf-8") for p in paths}
    return edits if all(compiles(v) for k, v in edits.items() if k.endswith(".py")) else None


def generate(snapshot: Snapshot, count: int, seed: str) -> list[PRSpec]:
    commits = mine_commits(snapshot.repo, snapshot.prefix, with_functions=False)
    base_time = next((c["timestamp"] for c in commits if c["sha"] == snapshot.base), None)
    fixes = [c for c in commits if c["is_fix"] and (base_time is None or c["timestamp"] <= base_time)]
    specs: list[PRSpec] = []
    for fix in reversed(fixes):  # most recent fixes first
        if len(specs) >= count:
            break
        changed = [f["path"] for f in fix["files"]]
        sources = [p for p in changed if not is_test_path(p)]
        tests = [p for p in changed if is_test_path(p)]
        subject = fix["message"].splitlines()[0]
        for variant, paths in (("source-only", sources), ("full-revert", sources + tests)):
            if not sources or (variant == "full-revert" and not tests):
                continue
            edits = _reverse_apply(snapshot, fix["sha"], paths)
            if edits is None:
                continue
            title = f"Revert \"{subject}\"" if variant == "full-revert" else f"Simplify {sources[0].rsplit('/', 1)[-1].removesuffix('.py')} logic"
            body = "This reverts the earlier change; it was causing confusion." if variant == "full-revert" else "Removes a special case that looked unnecessary."
            specs.append(PRSpec("", "", "", "C4", title, body, edits, f"revert-fix:{variant}",
                                {"fix_sha": fix["sha"], "fix_subject": subject, "issues": fix["issues"], "variant": variant}))
            if len(specs) >= count:
                break
    return specs
