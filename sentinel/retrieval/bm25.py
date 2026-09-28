"""BM25 keyword retrieval over chunks.

Uses the Lucene formulation of IDF, ``log(1 + (N - n + 0.5) / (n + 0.5))``, which stays
positive even when a term appears in half the corpus. Classic BM25 IDF goes to zero or
negative there, which silently drops matches in small repositories.
"""

from __future__ import annotations

import math
from collections import Counter

from sentinel.retrieval.text import tokenize

K1 = 1.2
B = 0.75


class BM25Index:
    def __init__(self, ids: list[str], texts: list[str]):
        self.ids = ids
        self.docs = [Counter(tokenize(t)) for t in texts]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        frequency: Counter = Counter()
        for doc in self.docs:
            frequency.update(doc.keys())
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in frequency.items()}

    def score(self, tokens: list[str], index: int) -> float:
        doc, length = self.docs[index], self.lengths[index]
        norm = K1 * (1 - B + B * length / self.avg_length) if self.avg_length else K1
        total = 0.0
        for term in tokens:
            tf = doc.get(term, 0)
            if tf:
                total += self.idf[term] * tf * (K1 + 1) / (tf + norm)
        return total

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        tokens = [t for t in tokenize(query) if t in self.idf]
        if not tokens:
            return []
        scored = [(self.ids[i], self.score(tokens, i)) for i in range(len(self.docs))]
        scored = [row for row in scored if row[1] > 0]
        return sorted(scored, key=lambda row: row[1], reverse=True)[:k]
