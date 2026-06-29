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


@dataclass(frozen=True)
class ArmorSchema:
    """Canonical column layout for an armor catalogue that ``find_tables``
    mis-segments. ``column_map`` is the heart: each entry ``(label, indices)``
    names an output column and the raw source-column index/indices whose cells are
    joined (with spaces) to fill it — so a name split across two cells (PF
    "Chain"|"shirt" → ``(0, 1)``), a value that spilled into the header's empty
    cell (Traveller protection note → ``(1, 2)``), and dropped junk/empty columns
    are all expressed as data, not special-cased in generic extraction code.

    ``apply`` only fires when the raw header has exactly ``raw_width`` columns, all
    ``detect`` markers are present, and no ``reject`` marker is — so a sibling
    layout of the same width (Traveller's STR/DEX/SLOTS powered armour vs. the
    mis-bucketed master table) is left untouched."""
    detect: tuple[str, ...]                          # whole-word header markers, ALL required (upper)
    column_map: tuple[tuple[str, tuple[int, ...]], ...]  # (output label, raw col indices joined)
    raw_width: int                                   # exact raw column count this layout has
    reject: tuple[str, ...] = ()                     # header markers that DISqualify the match

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(label for label, _ in self.column_map)

    def apply(self, rows: list[list[str]]) -> list[list[str]] | None:
        """Return ``rows`` remapped to ``columns`` if this is a matching armor
        table, else ``None`` (so the caller keeps the original)."""
        import re

        if not rows or len(rows[0]) != self.raw_width:
            return None
        hdr = " ".join(rows[0]).upper()
        if not all(re.search(rf"\b{re.escape(w)}\b", hdr) for w in self.detect):
            return None
        if any(re.search(rf"\b{re.escape(w)}\b", hdr) for w in self.reject):
            return None
        out: list[list[str]] = [list(self.columns)]
        for r in rows[1:]:
            if len(r) != self.raw_width:     # ragged row — leave untouched
                out.append(r)
                continue
            out.append([
                " ".join(r[i].strip() for i in idxs if i < len(r) and r[i] and r[i].strip())
                for _, idxs in self.column_map
            ])
        return out


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
    # Catalogue tables this source lays out in a way find_tables mis-segments
    # (name split across columns, mis-bucketed headers). Applied at index time to
    # repair the cell grid before routing/rendering — no re-extraction needed.
    armor_schema: ArmorSchema | None = None

    def matches(self, game: str) -> bool:
        g = (game or "").lower()
        return any(k in g for k in self.games)

    def reconstruct(self, page, page_no: int, existing: list) -> list:
        out: list = []
        for fn in self.reconstructors:
            out.extend(fn(page, page_no, existing) or [])
        return out

    def normalize_rows(self, rows: list[list[str]]) -> list[list[str]]:
        """Repair a mis-segmented catalogue grid using this source's schema(s),
        else return ``rows`` unchanged. Index-time, so a fix ships on a reindex
        without re-extracting the PDF."""
        if self.armor_schema:
            fixed = self.armor_schema.apply(rows)
            if fixed is not None:
                return fixed
        return rows


_REGISTRY: list[SourceProfile] = []


def register(profile: SourceProfile) -> None:
    _REGISTRY.append(profile)


def profile_for(game: str) -> SourceProfile | None:
    """The first registered profile whose game matches, else None (baseline only)."""
    for p in _REGISTRY:
        if p.matches(game):
            return p
    return None
