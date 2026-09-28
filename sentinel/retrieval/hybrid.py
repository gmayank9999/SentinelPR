"""Hybrid retrieval: graph neighbourhood + code search + BM25 + dense, fused with RRF and reranked.

``retrieval.mode`` selects which candidate generators run, which is how the retrieval
ablation (lexical / dense / graph / hybrid) is performed without touching agent code.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.config import Config
from sentinel.retrieval.bm25 import BM25Index
from sentinel.retrieval.chunker import Chunk
from sentinel.retrieval.dense import ChromaStore, DenseIndex, NumpyStore, make_embedder
from sentinel.retrieval.graph import CodeGraph, sym_id
from sentinel.retrieval.indexer import (
    code_chunks,
    history_documents,
    project_sources,
    retrieval_dir,
)
from sentinel.retrieval.lexical import make_code_search
from sentinel.retrieval.rerank import make_reranker
from sentinel.retrieval.rrf import reciprocal_rank_fusion

log = logging.getLogger(__name__)

MODES = {
    "lexical": ("search", "bm25"),
    "dense": ("dense",),
    "graph": ("graph",),
    "hybrid": ("graph", "search", "bm25", "dense"),
}


@dataclass
class Retrieved:
    id: str
    score: float
    sources: list[str]
    text: str
    path: str | None = None
    symbol: str | None = None
    kind: str = "code"
    meta: dict = field(default_factory=dict)


class RetrievalIndex:
    """All retrieval structures for one project, loaded from the index or built on the fly."""

    def __init__(self, cfg: Config, sources: dict[str, str], chunks: list[Chunk], docs: list[dict], graph: CodeGraph, dense_code, dense_history):
        self.cfg = cfg
        self.sources = sources
        self.chunks = {c.id: c for c in chunks}
        self.docs = {d["id"]: d for d in docs}
        self.graph = graph
        self.bm25_code = BM25Index([c.id for c in chunks], [c.text for c in chunks])
        self.bm25_history = BM25Index([d["id"] for d in docs], [d["text"] for d in docs])
        self.dense_code = dense_code
        self.dense_history = dense_history
        self.code_search = make_code_search(cfg, sources)
        self._by_symbol: dict[tuple[str, str], list[str]] = {}
        self._by_path: dict[str, list[Chunk]] = {}
        for c in chunks:
            self._by_path.setdefault(c.path, []).append(c)
            if c.symbol:
                self._by_symbol.setdefault((c.path, c.symbol), []).append(c.id)

    @classmethod
    def load(cls, cfg: Config, store=None) -> "RetrievalIndex":
        directory = retrieval_dir(cfg)
        sources = project_sources(cfg)
        embedder = make_embedder(cfg.get("retrieval.embedder", "hashing"), cfg.get("llm.providers.ollama.host"))
        chunks_file, docs_file, graph_file = directory / "chunks.json", directory / "history.json", directory / "graph.json"
        meta = json.loads((directory / "meta.json").read_text()) if (directory / "meta.json").exists() else {}

        # The PR's head may differ from the indexed base, so code chunks are always rebuilt from the
        # checked-out sources; embeddings are only reused when the stored index matches.
        chunks = code_chunks(sources)
        if docs_file.exists():
            docs = json.loads(docs_file.read_text(encoding="utf-8"))
        else:
            docs = history_documents(store) if store is not None else []
        if graph_file.exists() and store is None:
            graph = CodeGraph.load(graph_file)
        else:
            tests = [t["test_id"] for t in store.all_tests()] if store is not None else []
            graph = CodeGraph.build(sources, store, tests)

        dense_code = cls._dense(cfg, directory, "code", embedder, meta, chunks_file, [c.id for c in chunks], [c.text for c in chunks])
        dense_history = cls._dense(cfg, directory, "history", embedder, meta, docs_file, [d["id"] for d in docs], [d["text"] for d in docs])
        return cls(cfg, sources, chunks, docs, graph, dense_code, dense_history)

    @staticmethod
    def _dense(cfg, directory: Path, name: str, embedder, meta: dict, stored_file: Path, ids: list[str], texts: list[str]) -> DenseIndex:
        same_embedder = meta.get("embedder") == embedder.name
        if same_embedder and stored_file.exists():
            if meta.get("vector_stores", {}).get(name) == "chroma":
                try:
                    return DenseIndex(embedder, ChromaStore(directory, name))
                except Exception:
                    pass
            stored = NumpyStore.load(directory, name)
            if stored is not None and set(stored.ids) == set(ids):
                return DenseIndex(embedder, stored)
        store = NumpyStore()
        if ids:
            store.add(ids, embedder.embed(texts))
        return DenseIndex(embedder, store)

    # lookup helpers -----------------------------------------------------------
    def chunks_for(self, path: str, qualname: str) -> list[str]:
        ids = self._by_symbol.get((path, qualname))
        if ids:
            return ids
        # methods are chunked with their class only when very small; fall back to the enclosing chunk
        node = self.graph.node(sym_id(path, qualname))
        return [c.id for c in self._by_path.get(path, []) if node and c.start <= node.get("start", 0) <= c.end]

    def chunk_at(self, path: str, line: int) -> Chunk | None:
        best = None
        for c in self._by_path.get(path, []):
            if c.start <= line <= c.end and (best is None or c.end - c.start < best.end - best.start):
                best = c
        return best

    def text_of(self, item_id: str) -> str:
        if item_id in self.chunks:
            return self.chunks[item_id].text
        if item_id in self.docs:
            return self.docs[item_id]["text"]
        return ""


class HybridRetriever:
    def __init__(self, index: RetrievalIndex, cfg: Config | None = None):
        self.index = index
        self.cfg = cfg or index.cfg
        self.reranker = make_reranker(self.cfg.get("retrieval.reranker", True))
        self.queries: list[str] = []  # code-search queries issued, cited as evidence

    @property
    def mode(self) -> str:
        return self.cfg.get("retrieval.mode", "hybrid")

    @property
    def top_k(self) -> int:
        return int(self.cfg.get("retrieval.top_k", 10))

    def search_code(self, query: str, seeds: list[str] | None = None, mode: str | None = None, top_k: int | None = None) -> list[Retrieved]:
        mode = mode or self.mode
        top_k = top_k or self.top_k
        seeds = seeds or []
        pool = max(top_k * 3, 20)
        rankings: dict[str, list[str]] = {}
        active = MODES.get(mode, MODES["hybrid"])

        if "graph" in active and seeds:
            hops = int(self.cfg.get("retrieval.graph_hops", 2))
            distances = self.index.graph.neighbourhood(seeds, hops)
            ranked_nodes = sorted(distances, key=lambda n: (distances[n], n))
            rankings["graph"] = [cid for n in ranked_nodes if n.startswith("sym:") for cid in self._chunks_of_node(n)][:pool]
        if "search" in active:
            ranked: list[str] = []
            for name in self._search_terms(query, seeds):
                self.queries.append(name)
                for hit in self.index.code_search.references(name)[:pool]:
                    chunk = self.index.chunk_at(hit.path, hit.line)
                    if chunk is not None:
                        ranked.append(chunk.id)
            rankings["search"] = list(dict.fromkeys(ranked))[:pool]
        if "bm25" in active:
            rankings["bm25"] = [i for i, _ in self.index.bm25_code.search(query, pool)]
        if "dense" in active:
            rankings["dense"] = [i for i, _ in self.index.dense_code.search(query, pool)]
        return self._fuse(query, rankings, top_k, "code")

    def search_history(self, query: str, seeds: list[str] | None = None, top_k: int | None = None, mode: str | None = None) -> list[Retrieved]:
        """Commits, issues and PRs relevant to the query and linked to the seed symbols."""
        top_k = top_k or self.top_k
        mode = mode or self.mode
        pool = max(top_k * 3, 20)
        rankings: dict[str, list[str]] = {}
        if seeds and mode in ("graph", "hybrid"):
            rankings["graph"] = self._linked_history(seeds)[:pool]
        if mode in ("lexical", "hybrid"):
            rankings["bm25"] = [i for i, _ in self.index.bm25_history.search(query, pool)]
        if mode in ("dense", "hybrid"):
            rankings["dense"] = [i for i, _ in self.index.dense_history.search(query, pool)]
        return self._fuse(query, rankings, top_k, "history")

    # internals ----------------------------------------------------------------
    def _chunks_of_node(self, node: str) -> list[str]:
        data = self.index.graph.node(node)
        if not data:
            return []
        return self.index.chunks_for(data["path"], data["qualname"])

    def _search_terms(self, query: str, seeds: list[str]) -> list[str]:
        terms = [self.index.graph.node(s).get("name") for s in seeds]
        known = {n.split("::")[-1].split(".")[-1] for n in self.index.graph.g.nodes if n.startswith("sym:")}
        terms += [w for w in query.replace("(", " ").replace(")", " ").split() if w in known]
        return [t for t in dict.fromkeys(terms) if t and not t.startswith("<")][:8]

    def _linked_history(self, seeds: list[str]) -> list[str]:
        graph = self.index.graph
        ranked: list[str] = []
        for seed in seeds:
            for bug in graph.bug_history(seed):
                ranked.append(f"commit:{bug['fix'][:10]}")
                if bug.get("issue"):
                    ranked.append(f"issue:{bug['issue']}")
                ranked.append(f"commit:{bug['introducing_sha'][:10]}")
            for commit_node in reversed(graph.commits_for(seed)):  # most recent first
                sha = commit_node.split(":", 1)[1]
                ranked.append(f"commit:{sha[:10]}")
                for issue_node, _ in graph.edges_of_type(commit_node, "fixes"):
                    ranked.append(issue_node)
            for pr_node, _ in graph.edges_of_type(seed, "discussed_in"):
                ranked.append(pr_node)
        return [i for i in dict.fromkeys(ranked) if i in self.index.docs]

    def _fuse(self, query: str, rankings: dict[str, list[str]], top_k: int, kind: str) -> list[Retrieved]:
        fused = reciprocal_rank_fusion({k: v for k, v in rankings.items() if v})
        candidates = fused[: max(top_k * 3, 20)]
        if not candidates:
            return []
        texts = [self.index.text_of(i) for i, _, _ in candidates]
        final_scores = [s for _, s, _ in candidates]
        if self.reranker is not None:
            rerank = self.reranker.score(query, texts)
            final_scores = _blend(final_scores, rerank)
        order = sorted(range(len(candidates)), key=lambda i: final_scores[i], reverse=True)[:top_k]
        results = []
        for i in order:
            item_id, _, sources = candidates[i]
            chunk = self.index.chunks.get(item_id)
            doc = self.index.docs.get(item_id, {})
            results.append(
                Retrieved(
                    id=item_id,
                    score=round(final_scores[i], 4),
                    sources=sources,
                    text=texts[i],
                    path=chunk.path if chunk else None,
                    symbol=chunk.symbol if chunk else None,
                    kind=kind if chunk is None else "code",
                    meta={k: v for k, v in doc.items() if k not in ("text",)},
                )
            )
        return results


def _blend(first: list[float], second: list[float]) -> list[float]:
    def norm(values: list[float]) -> list[float]:
        lo, hi = min(values), max(values)
        return [0.5 if hi == lo else (v - lo) / (hi - lo) for v in values]

    a, b = norm(first), norm(second)
    return [0.5 * x + 0.5 * y for x, y in zip(a, b)]
