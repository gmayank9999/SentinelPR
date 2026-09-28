"""Reciprocal Rank Fusion (Cormack et al., 2009)."""

from __future__ import annotations

from collections import defaultdict

RRF_K = 60


def reciprocal_rank_fusion(rankings: dict[str, list[str]], k: int = RRF_K, weights: dict[str, float] | None = None) -> list[tuple[str, float, list[str]]]:
    """Fuse ranked id lists. Returns (id, score, contributing sources), best first."""
    scores: dict[str, float] = defaultdict(float)
    sources: dict[str, list[str]] = defaultdict(list)
    for source, ranked in rankings.items():
        weight = (weights or {}).get(source, 1.0)
        for rank, item in enumerate(dict.fromkeys(ranked), start=1):
            scores[item] += weight / (k + rank)
            sources[item].append(source)
    return sorted(((i, s, sources[i]) for i, s in scores.items()), key=lambda row: row[1], reverse=True)
