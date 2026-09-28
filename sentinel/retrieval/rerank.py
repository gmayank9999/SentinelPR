"""Second-stage reranking of fused candidates.

With sentence-transformers installed we use a cross-encoder (``BAAI/bge-reranker-base`` by
default). Otherwise a TF-IDF cosine over code-aware tokens re-scores the candidates, which
is cheap and still reorders the fused list by direct relevance to the query.
"""

from __future__ import annotations

import logging

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from sentinel.retrieval.text import tokenize

log = logging.getLogger(__name__)


class TfidfReranker:
    name = "tfidf"

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        vectorizer = TfidfVectorizer(tokenizer=tokenize, lowercase=False, token_pattern=None, sublinear_tf=True)
        try:
            matrix = vectorizer.fit_transform([query, *documents])
        except ValueError:  # empty vocabulary
            return [0.0] * len(documents)
        return cosine_similarity(matrix[0], matrix[1:]).ravel().tolist()


class CrossEncoderReranker:
    def __init__(self, model: str = "BAAI/bge-reranker-base"):
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model, device="cpu")
        self.name = f"cross-encoder:{model}"

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        return [float(s) for s in self.model.predict([(query, d[:2000]) for d in documents])]


def make_reranker(spec: str | bool | None):
    if spec in (None, False, "off"):
        return None
    if isinstance(spec, str) and spec.startswith("cross-encoder"):
        model = spec.split(":", 1)[1] if ":" in spec else "BAAI/bge-reranker-base"
        try:
            return CrossEncoderReranker(model)
        except Exception as exc:
            log.warning("cross-encoder unavailable (%s); using TF-IDF reranking", exc)
    return TfidfReranker()
