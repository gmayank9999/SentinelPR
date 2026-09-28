"""Shared generator plumbing: the PR spec and a view of the base revision's sources."""

from __future__ import annotations

import ast
import random
import re
from dataclasses import asdict, dataclass, field

from sentinel import gitutil
from sentinel.retrieval.chunker import Symbol, parse_python


@dataclass
class PRSpec:
    id: str
    repo: str
    base: str
    category: str
    title: str
    body: str
    edits: dict[str, str | None]  # project-relative path -> new content (None deletes)
    generator: str
    meta: dict = field(default_factory=dict)
    attack: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PRSpec":
        return cls(**data)


class Snapshot:
    """Read-only view of the project at the base revision."""

    def __init__(self, repo, base: str, prefix: str, package: str, tests: str = "tests"):
        self.repo, self.base, self.prefix, self.package, self.tests = repo, base, prefix, package, tests
        files = gitutil.list_files(repo, base, prefix.rstrip("/") if prefix else "")
        self.files = [f[len(prefix):] if prefix and f.startswith(prefix) else f for f in files]
        self._cache: dict[str, str | None] = {}

    def read(self, path: str) -> str | None:
        if path not in self._cache:
            self._cache[path] = gitutil.show_file(self.repo, self.base, self.prefix + path)
        return self._cache[path]

    @property
    def source_files(self) -> list[str]:
        root = self.package.replace(".", "/") + "/"
        return sorted(f for f in self.files if f.startswith(root) and f.endswith(".py") and not f.endswith("__init__.py"))

    @property
    def test_files(self) -> list[str]:
        return sorted(f for f in self.files if f.startswith(self.tests + "/") and f.rsplit("/", 1)[-1].startswith("test_"))

    def functions(self, path: str) -> list[Symbol]:
        text = self.read(path) or ""
        return [s for s in parse_python(text, path).symbols if s.kind in ("function", "method")]


def rng_for(*parts) -> random.Random:
    return random.Random("|".join(str(p) for p in parts))


def replace_lines(source: str, start: int, end: int, new_lines: list[str]) -> str:
    lines = source.split("\n")
    return "\n".join(lines[: start - 1] + new_lines + lines[end:])


def compiles(source: str) -> bool:
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def human_name(symbol: str) -> str:
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", symbol).replace("_", " ").strip().lower()


EXCLUDED_MODULES = ("/api/", "/ops/", "seed.py")


def interesting_sources(snapshot: Snapshot) -> list[str]:
    """Domain modules: the API wiring, ops plumbing and demo data are not mutation targets."""
    return [f for f in snapshot.source_files if not any(x in f for x in EXCLUDED_MODULES)]
