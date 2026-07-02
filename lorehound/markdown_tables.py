"""Harvest GitHub-flavoured pipe-tables from a document's extracted markdown.

pymupdf4llm's markdown conversion captures many multi-column tables *with their row and
column labels intact* — exactly the tables that ``find_tables`` (the geometric path that
feeds the ``tables`` field) delabels. High Guard's hull / drive / power construction
tables, for instance, come out of ``find_tables`` as bare number grids
(``['7', '2.5%', 'Cr50000']``) but sit in the markdown as clean, labelled tables
(``Titanium Steel | 7 | 2.5% | Cr50000 | …`` under a ``Hull Armour`` heading).

This recovers those labelled tables from the cached markdown, so a builder / lookup can
use them without re-extracting the PDF. It's deliberately conservative: it only returns
well-formed GFM tables (a header row, a ``|---|`` separator, at least one data row) and
does not try to reconcile them with the ``find_tables`` set — callers pick what they
need (e.g. the ship builder harvests construction tables by title).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PAGE = re.compile(r"\[\[page (\d+)\]\]")
_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_SEP_CELL = re.compile(r":?-{2,}:?")


def _clean(cell: str) -> str:
    """Strip markdown emphasis and collapse whitespace within a cell."""
    return re.sub(r"\s+", " ", cell.replace("*", "").replace("`", "")).strip()


def _split_row(line: str) -> list[str]:
    """``"|a|b|c|"`` → ``["a", "b", "c"]`` (drop the outer empty cells the pipes make)."""
    parts = line.split("|")
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [_clean(p) for p in parts]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_CELL.fullmatch(c) for c in cells if c != "")


def _dedupe_header_cell(cell: str) -> str:
    """Undo pymupdf4llm's habit of prepending the table's name to its first column
    header — ``"Hull Configuration Hull Configuration"`` → ``"Hull Configuration"``,
    ``"Hull Armour Armour"`` → ``"Hull Armour"``. Collapses adjacent duplicate words,
    then a whole doubled phrase (first half == second half)."""
    words = cell.split()
    collapsed: list[str] = []
    for w in words:
        if not collapsed or collapsed[-1].lower() != w.lower():
            collapsed.append(w)
    n = len(collapsed)
    if n and n % 2 == 0:
        half = n // 2
        if [w.lower() for w in collapsed[:half]] == [w.lower() for w in collapsed[half:]]:
            collapsed = collapsed[:half]
    return " ".join(collapsed)


@dataclass
class MarkdownTable:
    page: int
    title: str
    rows: list[list[str]] = field(default_factory=list)  # [header, *data]
    source: str = ""     # book the table came from (set by the caller)

    @property
    def header(self) -> list[str]:
        return self.rows[0] if self.rows else []


def _build(block: list[str], page: int, title: str) -> MarkdownTable | None:
    grid = [_split_row(ln) for ln in block]
    if len(grid) < 3 or not _is_separator(grid[1]):
        return None  # not a well-formed GFM table (need header, separator, ≥1 data row)
    header = [_dedupe_header_cell(grid[0][0])] + grid[0][1:] if grid[0] else grid[0]
    rows = [header] + grid[2:]
    if not any(any(c for c in r) for r in rows[1:]):
        return None  # no data
    return MarkdownTable(page=page, title=title, rows=rows)


def harvest_tables(text: str) -> list[MarkdownTable]:
    """Every well-formed GFM pipe-table in ``text``, each tagged with the page it sits on
    (the nearest preceding ``[[page N]]`` marker) and the nearest preceding heading as a
    title (the printed table name, e.g. ``Thrust Potential``)."""
    out: list[MarkdownTable] = []
    page, title = 0, ""
    block: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("|"):
            block.append(s)
            continue
        if block:
            t = _build(block, page, title)
            if t:
                out.append(t)
            block = []
        pm = _PAGE.match(s)
        if pm:
            page = int(pm.group(1))
        hm = _HEADING.match(s)
        if hm:
            title = _clean(hm.group(1))
    if block:
        t = _build(block, page, title)
        if t:
            out.append(t)
    return out
