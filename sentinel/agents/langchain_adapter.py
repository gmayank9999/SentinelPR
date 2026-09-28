"""LangChain integration: SentinelPR's hybrid retrieval as a LangChain retriever.

This lets the Q&A chain (and anyone building on LangChain) use graph + lexical + dense
retrieval with evidence ids carried in each Document's metadata.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever


class SentinelRetriever(BaseRetriever):
    """Retrieve code chunks and/or history documents (commits, issues, PRs)."""

    retriever: Any
    collections: tuple[str, ...] = ("code", "history")
    top_k: int = 6
    seeds: list[str] = []

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        results = []
        if "history" in self.collections:
            results += self.retriever.search_history(query, seeds=self.seeds, top_k=self.top_k)
        if "code" in self.collections:
            results += self.retriever.search_code(query, seeds=self.seeds, top_k=self.top_k)
        results.sort(key=lambda r: r.score, reverse=True)
        return [
            Document(page_content=r.text, metadata={"id": r.id, "score": r.score, "sources": r.sources, "path": r.path,
                                                    "symbol": r.symbol, "kind": r.kind, **{k: v for k, v in r.meta.items() if k in ("title", "ref")}})
            for r in results
        ]
