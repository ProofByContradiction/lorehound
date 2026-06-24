"""Per-source extraction profiles — the hybrid indexer architecture.

A generic baseline (find_tables + word-bucketing in :mod:`pdf_tables`) runs for
*every* book, so any book dropped into Drive still indexes. A :class:`SourceProfile`,
matched by its game (the top-level Drive folder), *adds* source-specific geometric
table reconstructors for layouts the generic pass can't recover — e.g. T2K's
column career-cards, or (later) Traveller's heading-anchored careers, gear, and
ship stat blocks. New/unknown sources simply get the baseline.

Profiles are registered by :mod:`pdf_tables` at import time (the reconstructors
are PyMuPDF-geometry functions that live there); this module just holds the data
structure + the registry, so it stays free of any heavy/fitz dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# A reconstructor: (page, page_no, tables_so_far) -> list of extra table dicts
# ({"page", "title", "rows"}). It may inspect what the generic pass already found
# (tables_so_far) to avoid duplicating a cleanly-detected table.
Reconstructor = Callable[[object, int, list], list]


@dataclass
class SourceProfile:
    name: str
    games: tuple[str, ...]                       # game-name substrings this matches
    reconstructors: list[Reconstructor] = field(default_factory=list)

    def matches(self, game: str) -> bool:
        g = (game or "").lower()
        return any(k in g for k in self.games)

    def reconstruct(self, page, page_no: int, existing: list) -> list:
        out: list = []
        for fn in self.reconstructors:
            out.extend(fn(page, page_no, existing) or [])
        return out


_REGISTRY: list[SourceProfile] = []


def register(profile: SourceProfile) -> None:
    _REGISTRY.append(profile)


def profile_for(game: str) -> SourceProfile | None:
    """The first registered profile whose game matches, else None (baseline only)."""
    for p in _REGISTRY:
        if p.matches(game):
            return p
    return None
