"""Configuration loading.

``config.yaml`` provides defaults; an optional ``sentinel.yaml`` at the repository root
overrides them, and environment variables of the form ``SENTINEL__SECTION__KEY`` win last.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PACKAGE_DIR / "config.yaml"
ENV_PREFIX = "SENTINEL__"


def _deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce(raw: str) -> Any:
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", ""}:
        return None
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def _env_overrides(environ: dict[str, str]) -> dict:
    overrides: dict = {}
    for key, value in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = [part.lower() for part in key[len(ENV_PREFIX) :].split("__") if part]
        node = overrides
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    return overrides


class Config:
    """Dictionary-backed configuration with dotted-path access: ``cfg.get("retrieval.top_k")``."""

    def __init__(self, data: dict, repo_root: Path):
        self.data = data
        self.repo_root = repo_root.resolve()

    @classmethod
    def load(
        cls,
        repo_root: Path | str = ".",
        path: Path | str | None = None,
        overrides: dict | None = None,
        environ: dict[str, str] | None = None,
    ) -> "Config":
        repo_root = Path(repo_root)
        data = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        user_file = Path(path) if path else repo_root / "sentinel.yaml"
        if user_file.exists():
            data = _deep_merge(data, yaml.safe_load(user_file.read_text(encoding="utf-8")) or {})
        data = _deep_merge(data, _env_overrides(dict(os.environ if environ is None else environ)))
        if overrides:
            data = _deep_merge(data, overrides)
        return cls(data, repo_root)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def with_overrides(self, overrides: dict) -> "Config":
        return Config(_deep_merge(self.data, overrides), self.repo_root)

    # Frequently used paths -------------------------------------------------
    @property
    def project_root(self) -> Path:
        return (self.repo_root / self.get("project.root", ".")).resolve()

    @property
    def project_prefix(self) -> str:
        """The project root relative to the repository root, as a posix prefix ('' for the root)."""
        rel = Path(self.get("project.root", ".")).as_posix().strip("/")
        return "" if rel in {"", "."} else rel + "/"

    @property
    def workdir(self) -> Path:
        path = Path(self.get("workdir", ".sentinel"))
        return path if path.is_absolute() else self.repo_root / path

    @property
    def history_repo(self) -> Path:
        configured = self.get("project.history_repo")
        if not configured:
            return self.repo_root
        path = Path(configured)
        return path if path.is_absolute() else self.repo_root / path

    @property
    def tracker_dir(self) -> Path | None:
        configured = self.get("project.tracker_dir")
        if not configured:
            return None
        path = Path(configured)
        return path if path.is_absolute() else self.repo_root / path
