"""Load ``KEY=value`` lines from a ``.env`` file into the process environment.

Rules: variables that are already set win (so a real environment variable always overrides the file),
blank values are ignored, quotes are stripped, and nothing is ever logged or returned except the *names*
that were loaded. The file is git-ignored; ``.env.example`` documents the names.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = LINE.match(raw)
        if not match:
            continue
        key, value = match.groups()
        if value[:1] in ("'", '"') and value.count(value[0]) >= 2:
            value = value[1:value.index(value[0], 1)]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()  # an inline comment needs a space before the '#'
        if value:
            values[key] = value
    return values


def load_env(path: Path | str = ".env") -> list[str]:
    """Set variables from ``path`` that are not already set. Returns the names that were loaded."""
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for key, value in parse(path.read_text(encoding="utf-8")).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
