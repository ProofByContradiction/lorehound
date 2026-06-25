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


@dataclass(frozen=True)
class CareerGeometry:
    """Page-layout coordinates (PDF points) for a heading-anchored career-spread
    reconstructor — see ``pdf_tables.traveller_career_sections``. The defaults
    match the Mongoose Traveller 2e two-column spread; a sourcebook laid out
    differently overrides them via its profile's ``career_geometry`` so the same
    reconstruction code works without hardcoded coordinates baked into it."""
    # x-range (left, right edges) of the full-width left-page band: Skills, Ranks.
    left_band: tuple[float, float] = (82.0, 560.0)
    # x-range of the right-column band: Career progress, Mustering out benefits.
    right_band: tuple[float, float] = (310.0, 565.0)
    # x-range of the roll | description band: Mishaps, Events.
    roll_band: tuple[float, float] = (70.0, 580.0)
    # Vertical divider between the two page columns: a section heading at x <=
    # this is left-column (Skills / Mishaps / Events); at x >= this it is
    # right-column (Career progress / Mustering out).
    column_split: float = 300.0
    # Ranks' heading runs a little further right than the other left-column ones,
    # so its left-column test allows a wider x.
    ranks_heading_max: float = 400.0
    # Roll-index column: numeric words left of this x are the roll keys (1D / 2D).
    roll_index_max: float = 110.0


@dataclass
class SourceProfile:
    name: str
    games: tuple[str, ...]                       # game-name substrings this matches
    reconstructors: list[Reconstructor] = field(default_factory=list)
    # Chapter-fallback routing for ``pdf_tables.classify_table``: when a table's
    # header gives no category signal, an exact (UPPERCASE) match on the table's
    # chapter routes it. Empty by default, so an unprofiled book never force-routes
    # on chapter name alone. Chapter strings are compared upper-cased.
    item_chapters: frozenset[str] = frozenset()        # e.g. {"EQUIPMENT"} -> /item
    transport_chapters: frozenset[str] = frozenset()   # e.g. {"VEHICLES"} -> /transport
    # Career-spread hooks (heading-anchored careers whose section sub-tables
    # find_tables shatters). ``career_detect(page) -> bool`` says a page is part of
    # a career spread; ``career_sections(page, page_no, geom) -> list[dict]``
    # returns the clean geometric reconstruction that replaces the mangled generic
    # tables, using ``geom`` (this profile's ``career_geometry``) for the layout.
    career_detect: Callable | None = None
    career_sections: Callable | None = None
    # Page-layout coordinates for ``career_sections`` (None when no career hooks).
    career_geometry: CareerGeometry | None = None

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
