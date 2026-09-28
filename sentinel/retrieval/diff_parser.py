"""Turn a git diff into file changes and *change units* (the functions/classes it touches)."""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from pathlib import Path

from sentinel import gitutil
from sentinel.models import ChangeUnit, FileChange
from sentinel.retrieval.chunker import ModuleInfo, Symbol, parse_python

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
RENAME_SIMILARITY = 0.8


def parse_unified_diff(text: str) -> list[FileChange]:
    """Parse ``git diff --unified=0`` output. Works with any context size."""
    changes: list[FileChange] = []
    current: FileChange | None = None
    old_line = new_line = 0
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            match = re.match(r"diff --git a/(.+?) b/(.+)$", raw)
            old_path, new_path = (match.group(1), match.group(2)) if match else (None, raw.split(" b/")[-1])
            current = FileChange(path=new_path, old_path=old_path)
            changes.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.status = "added"
            current.old_path = None
        elif raw.startswith("deleted file mode"):
            current.status = "removed"
        elif raw.startswith("rename from "):
            current.status = "renamed"
            current.old_path = raw[len("rename from ") :]
        elif raw.startswith("rename to "):
            current.path = raw[len("rename to ") :]
        elif raw.startswith("Binary files "):
            current.is_binary = True
        elif raw.startswith("--- ") or raw.startswith("+++ "):
            continue
        elif raw.startswith("@@"):
            match = HUNK.match(raw)
            if match:
                old_line, new_line = int(match.group(1)), int(match.group(3))
        elif raw.startswith("+"):
            current.added_lines.append(new_line)
            new_line += 1
        elif raw.startswith("-"):
            current.removed_lines.append(old_line)
            old_line += 1
        elif raw.startswith(" "):
            old_line += 1
            new_line += 1
    for change in changes:
        if change.status == "modified" and change.old_path and change.old_path != change.path:
            change.status = "renamed"
        if change.status in ("modified", "added"):
            change.old_path = change.old_path if change.status == "modified" else None
    return changes


def _units_for(
    path: str,
    old_info: ModuleInfo | None,
    new_info: ModuleInfo | None,
    change: FileChange,
    old_source: str,
    new_source: str,
) -> list[ChangeUnit]:
    old_symbols = {s.qualname: s for s in (old_info.symbols if old_info else [])}
    new_symbols = {s.qualname: s for s in (new_info.symbols if new_info else [])}
    old_lines, new_lines = old_source.splitlines(), new_source.splitlines()

    def blank(lines: list[str], n: int) -> bool:
        return not (0 < n <= len(lines)) or not lines[n - 1].strip()

    touched_new: dict[str, list[int]] = {}
    touched_old: dict[str, list[int]] = {}
    module_added: list[int] = []
    module_removed: list[int] = []

    for line in change.added_lines:
        symbol = new_info.innermost(line) if new_info else None
        if symbol is None:
            if not blank(new_lines, line):
                module_added.append(line)
        else:
            touched_new.setdefault(symbol.qualname, []).append(line)
    for line in change.removed_lines:
        symbol = old_info.innermost(line) if old_info else None
        if symbol is None:
            if not blank(old_lines, line):
                module_removed.append(line)
        else:
            touched_old.setdefault(symbol.qualname, []).append(line)

    units: dict[str, ChangeUnit] = {}

    def unit(qualname: str, change_type: str, old: Symbol | None, new: Symbol | None) -> ChangeUnit:
        sym = new or old
        return ChangeUnit(
            id=f"{path}::{qualname}",
            file=path,
            symbol=sym.name,
            qualname=qualname,
            kind=sym.kind,
            change_type=change_type,
            old_range=(old.start, old.end) if old else None,
            new_range=(new.start, new.end) if new else None,
            old_signature=old.signature if old else None,
            new_signature=new.signature if new else None,
        )

    for qualname in sorted(set(touched_new) | set(touched_old)):
        old, new = old_symbols.get(qualname), new_symbols.get(qualname)
        change_type = "modified" if old and new else ("added" if new else "removed")
        u = unit(qualname, change_type, old, new)
        u.changed_lines = sorted(touched_new.get(qualname, []))
        u.removed_lines = sorted(touched_old.get(qualname, []))
        units[qualname] = u

    # A symbol whose whole body moved to a new name is a rename, not an add + remove.
    added = [u for u in units.values() if u.change_type == "added"]
    removed = [u for u in units.values() if u.change_type == "removed"]
    for gone in removed:
        old_sym = old_symbols[gone.qualname]
        old_body = "\n".join(old_lines[old_sym.def_line : old_sym.end])
        for fresh in added:
            new_sym = new_symbols[fresh.qualname]
            new_body = "\n".join(new_lines[new_sym.def_line : new_sym.end])
            if old_sym.kind == new_sym.kind and difflib.SequenceMatcher(None, old_body, new_body).ratio() >= RENAME_SIMILARITY:
                fresh.change_type = "renamed"
                fresh.old_range = (old_sym.start, old_sym.end)
                fresh.old_signature = old_sym.signature
                fresh.removed_lines = gone.removed_lines
                units.pop(gone.qualname, None)
                added.remove(fresh)
                break

    if module_added or module_removed:
        units["<module>"] = ChangeUnit(
            id=f"{path}::<module>",
            file=path,
            symbol="<module>",
            qualname="<module>",
            kind="module",
            change_type="added" if change.status == "added" else ("removed" if change.status == "removed" else "modified"),
            changed_lines=sorted(module_added),
            removed_lines=sorted(module_removed),
        )
    return sorted(units.values(), key=lambda u: (u.new_range or u.old_range or (0, 0))[0])


def change_units_for_file(change: FileChange, old_source: str | None, new_source: str | None) -> list[ChangeUnit]:
    if change.is_binary or not change.path.endswith(".py"):
        return []
    old_info = parse_python(old_source, change.old_path or change.path) if old_source is not None else None
    new_info = parse_python(new_source, change.path) if new_source is not None else None
    return _units_for(change.path, old_info, new_info, change, old_source or "", new_source or "")


def compute_change_units(
    changes: list[FileChange],
    read_old: Callable[[str], str | None],
    read_new: Callable[[str], str | None],
) -> list[ChangeUnit]:
    units: list[ChangeUnit] = []
    for change in changes:
        old = read_old(change.old_path) if change.old_path and change.status != "added" else None
        new = read_new(change.path) if change.status != "removed" else None
        units.extend(change_units_for_file(change, old, new))
    return units


class DiffContext:
    """Everything the pipeline needs to know about the change under review."""

    def __init__(self, repo: Path, base: str, head: str | None, prefix: str = ""):
        self.repo = Path(repo)
        self.base = base
        self.head = head  # None -> working tree
        self.prefix = prefix

    def _strip(self, path: str) -> str:
        return path[len(self.prefix) :] if self.prefix and path.startswith(self.prefix) else path

    def raw_diff(self) -> str:
        scope = [self.prefix.rstrip("/")] if self.prefix else None
        if self.head is None:
            args = ["diff", "--no-color", "--no-ext-diff", "-M", "--unified=0", self.base]
            if scope:
                args += ["--", *scope]
            return gitutil.git(self.repo, *args)
        return gitutil.diff_between(self.repo, self.base, self.head, scope)

    def file_changes(self) -> list[FileChange]:
        changes = parse_unified_diff(self.raw_diff())
        for change in changes:
            change.path = self._strip(change.path)
            if change.old_path:
                change.old_path = self._strip(change.old_path)
        return changes

    def read_old(self, path: str) -> str | None:
        return gitutil.show_file(self.repo, self.base, self.prefix + path)

    def read_new(self, path: str) -> str | None:
        if self.head is None:
            target = self.repo / (self.prefix + path)
            return target.read_text(encoding="utf-8") if target.exists() else None
        return gitutil.show_file(self.repo, self.head, self.prefix + path)

    def change_units(self, changes: list[FileChange] | None = None) -> list[ChangeUnit]:
        return compute_change_units(changes or self.file_changes(), self.read_old, self.read_new)
