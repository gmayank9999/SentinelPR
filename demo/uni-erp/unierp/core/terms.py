"""Academic term helpers. Terms are written as ``YYYY-SEASON``, e.g. ``2026-FALL``."""

from __future__ import annotations

from dataclasses import dataclass

from unierp.core.errors import ValidationError

SEASONS = ("SPRING", "SUMMER", "FALL")


@dataclass(frozen=True, order=True)
class Term:
    year: int
    season_index: int

    @property
    def season(self) -> str:
        return SEASONS[self.season_index]

    def __str__(self) -> str:
        return f"{self.year}-{self.season}"

    def next(self) -> "Term":
        if self.season_index == len(SEASONS) - 1:
            return Term(self.year + 1, 0)
        return Term(self.year, self.season_index + 1)


def parse_term(value: str) -> Term:
    try:
        year_text, season = value.strip().upper().split("-", 1)
        year = int(year_text)
        index = SEASONS.index(season)
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"invalid term: {value!r}") from exc
    if not 2000 <= year <= 2100:
        raise ValidationError(f"term year out of range: {year}")
    return Term(year, index)


def term_sort_key(value: str) -> Term:
    return parse_term(value)


def sort_terms(values) -> list[str]:
    return sorted(set(values), key=term_sort_key)


def is_before(a: str, b: str) -> bool:
    return parse_term(a) < parse_term(b)
