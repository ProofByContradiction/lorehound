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
    """Canonical column layout for a defensive-gear catalogue (armor or shields)
    that ``find_tables`` mis-segments — including a merely garbled header over
    already-aligned data, where the ``column_map`` is an identity relabel.
    ``column_map`` is the heart: each entry ``(label, indices)``
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


@dataclass(frozen=True)
class GradeSplit:
    """Explode a catalogue row that crams several tech-level grades into one
    cell-row — Traveller armour stacks the Basic/Improved/Advanced variants of a
    suit together: a name like ``Battle Dress, Basic Battle Dress, Improved Battle
    Dress, Advanced`` over stats ``+22 +25 +28`` / ``Cr200000 Cr220000 Cr440000``.

    The split is deliberately conservative — it never invents a wrong sub-value:
      • N (the grade count) is the whitespace-token count of the ``count_label``
        column (TL — one plain integer per grade).
      • The name splits on its repeated ``base,`` grade list; a single clean base
        name (no repeated word) instead gets a ``(<count_label><n>)`` suffix; an
        unparseable / crammed name leaves the whole row merged.
      • Each remaining cell is distributed one-token-per-grade only when it holds
        exactly N tokens, or N *identical* chunks (``Vacc Suit 1 Vacc Suit 1 Vacc
        Suit 1``); anything else (a ragged ``Vacc Suit 0 None`` skill, a protection
        value carrying a note) is kept whole on every grade rather than guessed."""
    detect: tuple[str, ...]      # whole-word header markers (upper) identifying an explodable table
    count_label: str             # header label whose cell token-count gives N (e.g. "TL")

    def apply(self, rows: list[list[str]]) -> list[list[str]]:
        import re

        if not rows or len(rows) < 2:
            return rows
        hdr = " ".join(rows[0]).upper()
        if not all(re.search(rf"\b{re.escape(w)}\b", hdr) for w in self.detect):
            return rows
        ccol = next(
            (j for j, h in enumerate(rows[0]) if h.strip().upper() == self.count_label.upper()),
            None,
        )
        if ccol is None:
            return rows
        out: list[list[str]] = [rows[0]]
        changed = False
        for r in rows[1:]:
            grades = self._split_row(r, ccol)
            if grades is None:
                out.append(r)
            else:
                out.extend(grades)
                changed = True
        return out if changed else rows

    def _split_row(self, row: list[str], ccol: int) -> list[list[str]] | None:
        if ccol >= len(row):
            return None
        n = len(row[ccol].split())
        if n < 2:
            return None
        names = self._split_name(row, ccol, n)
        if names is None:
            return None
        return [
            [names[gi]] + [self._cell(row[j], n)[gi] for j in range(1, len(row))]
            for gi in range(n)
        ]

    @staticmethod
    def _cell(cell: str, n: int) -> list[str]:
        """One value per grade. A clean N-token cell distributes one token each.
        Otherwise, if the cell divides into N equal chunks that share a common
        first token — the parallel ``Vacc Suit 1 Vacc Suit 0 Vacc Suit 0`` skill
        shape — distribute those chunks. Anything ragged (``Vacc Suit 0 None``, a
        protection value carrying a note) stays whole on every grade rather than
        risk a wrong sub-value."""
        toks = cell.split()
        if len(toks) == n:
            return toks
        if toks and len(toks) % n == 0:
            size = len(toks) // n
            chunks = [toks[i * size : (i + 1) * size] for i in range(n)]
            if len({c[0] for c in chunks}) == 1:    # uniform shape → safe to distribute
                return [" ".join(c) for c in chunks]
        return [cell] * n

    def _split_name(self, row: list[str], ccol: int, n: int) -> list[str] | None:
        import re

        name = (row[0] or "").strip()
        if "," in name:                                   # "<base>, <g1> <base>, <g2> ..."
            base = name.split(",", 1)[0].strip()
            if base:
                parts = [p.strip() for p in re.split(rf"(?={re.escape(base)},)", name) if p.strip()]
                if len(parts) == n:
                    return parts
        words = re.findall(r"[A-Za-z]{3,}", name)         # single clean base name → (TLn) suffix
        if name and len(name.split()) <= 4 and len(words) == len({w.lower() for w in words}):
            grades = row[ccol].split()
            return [f"{name} ({self.count_label}{grades[i]})" for i in range(n)]
        return None                                       # crammed / unparseable — leave merged


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
    # (name split across columns, mis-bucketed/garbled headers) — armor and
    # shields. Each is width-gated, so the first one that matches a grid wins;
    # applied at index time to repair the cell grid before routing/rendering, so a
    # fix ships on a reindex with no re-extraction.
    table_schemas: tuple[ArmorSchema, ...] = ()
    # Catalogue rows that stack several tech-level grades into one (Traveller
    # armour). Exploded into one row per grade after the schema repair.
    grade_split: GradeSplit | None = None

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
        for schema in self.table_schemas:
            fixed = schema.apply(rows)
            if fixed is not None:    # width-gated, so at most one schema matches
                rows = fixed
                break
        if self.grade_split:
            rows = self.grade_split.apply(rows)
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
