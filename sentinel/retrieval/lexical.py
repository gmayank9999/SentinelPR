"""Exact, regex and symbol search: Zoekt, Sourcegraph, or a local scan.

All three backends return :class:`SearchHit` rows so the hybrid retriever does not care
which one answered. Every query is recorded so it can be cited as evidence
(``search:<backend>:<query>``).
"""

from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


_IMPORT = re.compile(r"^\s*(from\s+\S+\s+)?import\s")


def _usages(hits: list["SearchHit"], symbol: str) -> list["SearchHit"]:
    """Drop the symbol's own definition and bare import lines: they are not usages."""
    definition = re.compile(rf"^\s*(async\s+)?(def|class)\s+{re.escape(symbol)}\b")
    return [h for h in hits if not definition.match(h.text) and not _IMPORT.match(h.text)]


@dataclass
class SearchHit:
    path: str
    line: int
    text: str
    backend: str
    query: str

    @property
    def evidence_id(self) -> str:
        return f"search:{self.backend}:{self.query}"


class LocalCodeSearch:
    """Scans the project's files. Always available; fine for repositories of this size."""

    backend = "local"

    def __init__(self, root: Path, sources: dict[str, str]):
        self.root = root
        self.sources = sources

    def search(self, query: str, *, regex: bool = False, word: bool = True, limit: int = 200) -> list[SearchHit]:
        pattern = query if regex else re.escape(query)
        if word and not regex:
            pattern = rf"\b{pattern}\b"
        compiled = re.compile(pattern)
        hits: list[SearchHit] = []
        for path in sorted(self.sources):
            for number, line in enumerate(self.sources[path].splitlines(), start=1):
                if compiled.search(line):
                    hits.append(SearchHit(path, number, line.strip(), self.backend, query))
                    if len(hits) >= limit:
                        return hits
        return hits

    def references(self, symbol: str) -> list[SearchHit]:
        """Usages of ``symbol`` that are not its own definition."""
        return _usages(self.search(symbol), symbol)


class ZoektSearch:
    backend = "zoekt"

    def __init__(self, url: str, repo_filter: str | None = None, prefix: str = ""):
        self.client = httpx.Client(base_url=url.rstrip("/"), timeout=10)
        self.repo_filter = repo_filter
        self.prefix = prefix

    def search(self, query: str, *, regex: bool = False, word: bool = True, limit: int = 200) -> list[SearchHit]:
        q = query if regex else f"\\b{re.escape(query)}\\b" if word else re.escape(query)
        if self.repo_filter:
            q += f" r:{self.repo_filter}"
        response = self.client.get("/api/search", params={"q": q, "num": limit})
        response.raise_for_status()
        result = response.json().get("result", {}) or {}
        hits = []
        for file_match in result.get("FileMatches") or []:
            path = file_match.get("FileName", "")
            if self.prefix and not path.startswith(self.prefix):
                continue
            path = path[len(self.prefix) :]
            for line_match in file_match.get("LineMatches") or []:
                text = base64.b64decode(line_match.get("Line", "")).decode("utf-8", "replace")
                hits.append(SearchHit(path, line_match.get("LineNumber", 0), text.strip(), self.backend, query))
        return hits[:limit]

    def references(self, symbol: str) -> list[SearchHit]:
        return _usages(self.search(symbol), symbol)


SOURCEGRAPH_QUERY = """
query Search($query: String!) {
  search(query: $query, version: V3) {
    results {
      results {
        __typename
        ... on FileMatch {
          file { path }
          lineMatches { lineNumber preview }
        }
      }
    }
  }
}
"""


class SourcegraphSearch:
    """Sourcegraph GraphQL search (a lab instance, or sourcegraph.com for public repositories)."""

    backend = "sourcegraph"

    def __init__(self, url: str, repo: str, prefix: str = "", token: str | None = None):
        headers = {}
        token = token or os.getenv("SRC_ACCESS_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"
        self.client = httpx.Client(base_url=url.rstrip("/"), timeout=15, headers=headers)
        self.repo = repo
        self.prefix = prefix

    def search(self, query: str, *, regex: bool = False, word: bool = True, limit: int = 200) -> list[SearchHit]:
        pattern = query if regex else (f"\\b{re.escape(query)}\\b" if word else re.escape(query))
        sg_query = f"repo:^{re.escape(self.repo)}$ {pattern} patternType:regexp count:{limit}"
        if self.prefix:
            sg_query += f" file:^{re.escape(self.prefix)}"
        response = self.client.post("/.api/graphql", json={"query": SOURCEGRAPH_QUERY, "variables": {"query": sg_query}})
        response.raise_for_status()
        hits = []
        for item in response.json()["data"]["search"]["results"]["results"]:
            if item.get("__typename") != "FileMatch":
                continue
            path = item["file"]["path"]
            path = path[len(self.prefix) :] if self.prefix and path.startswith(self.prefix) else path
            for match in item.get("lineMatches") or []:
                hits.append(SearchHit(path, match["lineNumber"] + 1, match["preview"].strip(), self.backend, query))
        return hits[:limit]

    def references(self, symbol: str) -> list[SearchHit]:
        return _usages(self.search(symbol), symbol)


def make_code_search(cfg, sources: dict[str, str]):
    """Zoekt when configured, then Sourcegraph, then the local scanner."""
    prefix = cfg.project_prefix
    zoekt = cfg.get("retrieval.zoekt_url")
    if zoekt:
        try:
            engine = ZoektSearch(zoekt, os.getenv("GITHUB_REPOSITORY"), prefix)
            engine.search("def", limit=1)
            return engine
        except httpx.HTTPError as exc:
            log.warning("zoekt unavailable (%s); trying the next backend", exc)
    sourcegraph = cfg.get("retrieval.sourcegraph_url")
    repository = cfg.get("retrieval.sourcegraph_repo") or os.getenv("GITHUB_REPOSITORY")
    if sourcegraph and repository:
        return SourcegraphSearch(sourcegraph, f"github.com/{repository}", prefix)
    return LocalCodeSearch(cfg.project_root, sources)
