"""Code-aware tokenisation shared by BM25, the hashing embedder and the fallback reranker."""

from __future__ import annotations

import re

from sentinel.retrieval.chunker import split_identifier

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")

STOPWORDS = frozenset(
    """
    a an and are as at be by for from if in into is it of on or that the this to was were will with
    def class return self cls none true false import pass else elif not
    """.split()
)


_SUFFIXES = ("ing", "ies", "ed", "es", "ly", "s")


def stem(word: str) -> str:
    """A deliberately light suffix stripper: 'rounded' -> 'round', 'rupees' -> 'rupee'."""
    if len(word) <= 4:
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "ies":
                return word[:-3] + "y"
            if suffix == "es" and not word.endswith(("ses", "xes", "zes", "ches", "shes")):
                return word[:-1]
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    """Identifiers are kept whole *and* split into sub-words, so ``calculateStudentCredits``
    matches queries for "student credits" as well as the exact name."""
    tokens: list[str] = []
    for raw in _TOKEN.findall(text):
        lowered = raw.lower()
        parts = split_identifier(raw)
        if len(parts) > 1:
            tokens.append(lowered)
        for part in parts:
            if part not in STOPWORDS and len(part) > 1:
                tokens.append(stem(part))
    return tokens
