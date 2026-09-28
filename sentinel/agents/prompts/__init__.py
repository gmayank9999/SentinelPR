"""Versioned prompt templates.

Each prompt lives in ``<name>.v<N>.md`` with ``## system`` and ``## user`` sections and
``$placeholders`` (``string.Template`` syntax, so JSON examples need no escaping). The
highest version is used unless one is pinned in config (``prompts.<name>: v1``), and the
name@version is recorded with every run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parent
_FILE = re.compile(r"^(?P<name>[a-z_]+)\.v(?P<version>\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    system: str
    user: str

    @property
    def id(self) -> str:
        return f"{self.name}@v{self.version}"

    def render(self, **values) -> tuple[str, str]:
        return Template(self.system).safe_substitute(values), Template(self.user).safe_substitute(values)


def available() -> dict[str, list[int]]:
    versions: dict[str, list[int]] = {}
    for path in PROMPT_DIR.glob("*.md"):
        match = _FILE.match(path.name)
        if match:
            versions.setdefault(match["name"], []).append(int(match["version"]))
    return {k: sorted(v) for k, v in versions.items()}


def load_prompt(name: str, version: int | str | None = None) -> Prompt:
    versions = available().get(name)
    if not versions:
        raise KeyError(f"no prompt named {name!r}")
    if version is None:
        chosen = versions[-1]
    else:
        chosen = int(str(version).lstrip("v"))
    text = (PROMPT_DIR / f"{name}.v{chosen}.md").read_text(encoding="utf-8")
    sections = re.split(r"^## (system|user)\s*$", text, flags=re.MULTILINE)
    parts = {sections[i]: sections[i + 1].strip() for i in range(1, len(sections) - 1, 2)}
    return Prompt(name, chosen, parts.get("system", ""), parts.get("user", ""))
