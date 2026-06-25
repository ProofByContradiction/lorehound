"""Render structured tables (cell grids) as clean monospace blocks for Discord.

Tables are recovered upstream as real cell grids by ``pdf_tables`` (PyMuPDF
find_tables + word-bucketing), so there's no reconstruction here — just
width-aware rendering. The style is deliberately *borderless*: a bold header,
one underline rule, then rows with no per-cell boxes or inter-row separators,
which reads far cleaner than a full grid (especially for wordy tables). Output
is an ``ansi`` code block so the header can be emphasized.
"""

from __future__ import annotations

import re
import textwrap

from .text_utils import acronym_title as _label
from .text_utils import clean_grid

_WORD = re.compile(r"[a-z0-9]+")

_ESC = "\x1b"
_HEAD = f"{_ESC}[1;36m"  # bold cyan header row
_RESET = f"{_ESC}[0m"
_GAP = " "               # column separator — a single space (save horizontal width)
# Prefer single-line rows: a generous width ceiling so cells wrap only when a table
# is genuinely too wide. Discord code blocks scroll horizontally, so wide-but-
# single-line beats narrow-but-wrapped for legibility.
_BUDGET = 100
_MIN_COL = 3
_MAX_COL = 40           # a single column only wraps past this many characters
_REPEAT_MIN = 25        # tables longer than this re-print the header as you scroll
_REPEAT_EVERY = 18      # …every this many body rows (pseudo-sticky header)
# A 3+ column table wider than this (after squeezing) can't fit a phone — ANSI
# code blocks squish, they don't side-scroll on Discord mobile — so it renders as
# reflowing markdown records instead. Narrower tables stay crisp ANSI columns.
_MOBILE_GRID_MAX = 42

# A die-roll column header: "D10", "2D6", "1D", "D66", "D100", or "Roll".
_DIE_HDR = re.compile(r"(?i)^(roll|\d*d\d+|\d*d)$")

# A "stat block" is the transpose of a catalogue: its *rows* are the stats (the
# first column is a label) and there's no header row. Traveller renders each ship
# this way — first column Hull / Armour / M-Drive… with the ship name as a page
# heading, not a column. We render these as a Stat | Value card, not a catalogue.
_SHIP_PARTS = frozenset({
    "hull", "armour", "armor", "m-drive", "j-drive", "manoeuvre drive",
    "jump drive", "power plant", "power", "bridge", "computer", "sensors",
    "staterooms", "stateroom", "cargo", "fuel", "fuel tanks", "weapons",
    "weapon", "systems", "software", "common areas", "airlock", "drive",
    "screens", "turret", "hardpoints", "hardpoint", "armoured bulkheads",
})


def is_ship_statblock(rows: list[list[str]]) -> bool:
    """True if ``rows`` is a component stat block (first column is ship-system
    labels — Hull, Armour, M-Drive…) rather than a catalogue of named items."""
    col0 = [(r[0] or "").strip().lower() for r in rows if r and (r[0] or "").strip()]
    if len(col0) < 4:
        return False
    hits = sum(1 for c in col0 if c in _SHIP_PARTS)
    return hits >= max(3, len(col0) // 2)


def _wrap_cell(text: str, width: int) -> list[str]:
    if not text:
        return [""]
    return textwrap.wrap(
        text, width=width, break_long_words=True, break_on_hyphens=False
    ) or [""]


def _fit_widths(nat: list[int], budget: int) -> list[int]:
    """Water-fill column widths: the largest uniform cap ``W`` such that the
    columns (each clamped to ``W``) still fit ``budget``. Narrow columns keep
    their natural width; only the wide ones get clamped (and wrap)."""
    if sum(nat) <= budget:
        return nat[:]
    lo, hi, best = _MIN_COL, max(nat), _MIN_COL
    while lo <= hi:
        mid = (lo + hi) // 2
        if sum(min(n, mid) for n in nat) <= budget:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return [min(n, best) for n in nat]


def _toks(s: str) -> set:
    return {t for t in _WORD.findall(s.lower()) if len(t) > 1}


def _name_col(grid: list[list[str]]) -> int:
    """Index of the column holding row names — the first column whose values are
    mostly *alphabetic* (a name/label column), skipping leading roll/index columns
    whose values are numeric (e.g. a D10). Short names like 'M16' still qualify."""
    ncols = len(grid[0]) if grid else 0
    for j in range(ncols):
        vals = [grid[r][j] for r in range(1, len(grid)) if j < len(grid[r]) and grid[r][j]]
        if vals and sum(any(c.isalpha() for c in v) for v in vals) / len(vals) >= 0.5:
            return j
    return 0


def _render_vertical(grid: list[list[str]]) -> str:
    """Render a (too-wide-for-a-phone) table as reflowing markdown records — the
    only mobile-safe option for wide data (Discord wraps markdown, not code blocks).

    A roll/outcome table (leading die column) gets a ``**Die** (D10)`` header and
    leads each record with the roll value in a monospaced left column; the primary
    name is bold and the remaining columns become ``**Label:** value`` fields."""
    header = grid[0]
    ncols = len(header)
    name_col = _name_col(grid)
    has_roll = name_col > 0  # leading code/roll columns precede the name column

    caption, pad = "", 1
    if has_roll:
        die_hdr = header[0].strip()
        m = re.search(r"\(([^)]+)\)", die_hdr)  # "Roll (D10)" -> "D10"
        caption = f"**Die** *({m.group(1) if m else (die_hdr or 'Die')})*"
        pad = max(
            (len(" ".join(grid[r][j] for j in range(name_col) if j < len(grid[r]) and grid[r][j]))
             for r in range(1, len(grid))),
            default=1,
        )

    blocks = []
    for r in range(1, len(grid)):
        row = grid[r]
        roll = " ".join(row[j] for j in range(name_col) if j < len(row) and row[j])
        name = row[name_col] if name_col < len(row) else ""
        fields = [
            f"**{_label(header[j])}:** {row[j]}" if header[j] else row[j]
            for j in range(name_col + 1, ncols)
            if j < len(row) and row[j]
        ]
        if has_roll:
            lead = f"`{roll.ljust(pad)}` **{name}**" if name else f"`{roll.ljust(pad)}`"
        else:
            lead = f"**{name}**" if name else ""
        block = lead
        if fields:
            block += ("\n" if lead else "") + " · ".join(fields)
        blocks.append(block or "—")
    body = "\n\n".join(blocks)
    return f"{caption}\n\n{body}" if caption else body


def _roll_key_col(grid: list[list[str]]) -> int | None:
    """Index of a leading die-roll column (header like ``D10`` / ``2D6`` / ``Roll``),
    else None. We relabel it ``Roll (Dn)`` but keep every other column."""
    head = grid[0] if grid else []
    return 0 if head and _DIE_HDR.match(head[0].strip()) else None


def _stat_card(pairs: list[tuple[str, str]]) -> tuple[str, bool]:
    """Render (label, value) pairs as a 2-column ``Stat | Value`` card. Empty
    values are dropped; returns ``("", False)`` when nothing survives so callers
    can fall back to the whole table. Uses _render_grid directly (not
    render_table) so the 2-column result can't recurse back into stat-block
    detection. Returns ``(block, wide)`` — the honest width flag from _render_grid."""
    stats = [["Stat", "Value"]]
    for label, value in pairs:
        if label and value:
            stats.append([label, value])
    if len(stats) < 2:
        return "", False
    return _render_grid(stats, _BUDGET)


def _statblock_card(grid: list[list[str]]) -> tuple[str, bool]:
    """Render a component stat block (rows *are* the stats — first column is a
    label, no header row) as a Stat | Value card: col0 is the label, the rest of
    the row joined is the value."""
    return _stat_card(
        [(row[0].strip(), " ".join(c.strip() for c in row[1:] if c.strip())) for row in grid]
    )


def render_table(rows: list[list[str]]) -> tuple[str, bool]:
    """Render a cell grid (first row = header) as a clean monospace table.

    **Width decides format** (Discord mobile squishes wide code blocks rather than
    side-scrolling, so we can't rely on scroll): a table that fits a phone renders
    as crisp ANSI columns; a wider 3+ column table reflows into markdown records —
    a bold lead (roll · name) plus ``**Label:** value`` fields — which Discord
    wraps to the viewport. Spacing is squeezed (single-space gaps, collapsed cell
    whitespace) so more tables stay columnar.

    Returns ``(code_block_or_records, wide)``.
    """
    grid = [
        [" ".join((c or "").split()) for c in r]  # strip + collapse internal whitespace
        for r in rows
        if any((c or "").strip() for c in r)
    ]
    if not grid:
        return "```\n(empty table)\n```", False
    # A component stat block (e.g. a Traveller ship that landed in a non-spacecraft
    # chapter) is the transpose of a table — render it as a Stat | Value card, not
    # as wide reflowed records (which mangle it).
    if is_ship_statblock(grid):
        block, wide = _statblock_card(grid)
        if block:
            return block, wide
    ncols = max(len(r) for r in grid)
    grid = [r + [""] * (ncols - len(r)) for r in grid]

    # Relabel a leading die column "Roll (Dn)" (used by the columnar path; the
    # records path leads with the roll value instead).
    roll_idx = _roll_key_col(grid)
    if roll_idx is not None:
        die = grid[0][roll_idx]
        grid[0][roll_idx] = die if die.lower().startswith("roll") else f"Roll ({die})"

    natural = [max((len(grid[r][j]) for r in range(len(grid))), default=0) for j in range(ncols)]
    compact = sum(min(n, _MAX_COL) for n in natural) + len(_GAP) * (ncols - 1)
    if ncols >= 3 and compact > _MOBILE_GRID_MAX:
        return _render_vertical(grid), False
    return _render_grid(grid, _BUDGET)


def _render_grid(grid: list[list[str]], budget: int) -> tuple[str, bool]:
    """Render a cell grid as an aligned ``ansi`` code block within ``budget`` columns
    (water-filling wide columns so they wrap). Returns ``(code_block, wide)``."""
    ncols = max(len(r) for r in grid)
    grid = [r + [""] * (ncols - len(r)) for r in grid]
    nat = [
        max(_MIN_COL, min(_MAX_COL, max((len(grid[r][i]) for r in range(len(grid))), default=0)))
        for i in range(ncols)
    ]
    gap = len(_GAP) * (ncols - 1)
    widths = _fit_widths(nat, max(budget - gap, ncols * _MIN_COL))

    # Centre short "code" columns (die rolls, modifiers); left-align wordy ones.
    aligns = []
    for i in range(ncols):
        col = [grid[r][i] for r in range(len(grid)) if grid[r][i]]
        aligns.append("c" if col and all(len(c) <= 4 for c in col) else "l")

    def fmt_row(r: int) -> list[str]:
        wrapped = [_wrap_cell(grid[r][i], widths[i]) for i in range(ncols)]
        height = max(len(c) for c in wrapped)
        out = []
        for li in range(height):
            parts = []
            for i in range(ncols):
                txt = wrapped[i][li] if li < len(wrapped[i]) else ""
                txt = txt.center(widths[i]) if aligns[i] == "c" else txt.ljust(widths[i])
                parts.append(txt)
            out.append(_GAP.join(parts).rstrip())
        return out

    # Header block (bold, may wrap) + the underline rule.
    head_block = [f"{_HEAD}{ln}{_RESET}" for ln in fmt_row(0)]
    head_block.append(_GAP.join("─" * widths[i] for i in range(ncols)))

    # Discord can't freeze a header on scroll, so on long tables re-print it every
    # few rows — a header stays within view as you scroll. Short tables print once.
    body = list(range(1, len(grid)))
    repeat = len(body) > _REPEAT_MIN
    lines = list(head_block)
    for n, r in enumerate(body):
        if repeat and n and n % _REPEAT_EVERY == 0:
            lines += head_block
        lines += fmt_row(r)

    wide = ncols >= 6 or (sum(widths) + gap) > 58
    return "```ansi\n" + "\n".join(lines) + "\n```", wide


def match_row(rows: list[list[str]], query: str) -> int | None:
    """Index of the data row whose name clearly matches the query, else None — used
    to pull a single item ("M82A1") out of a stat table as its own card."""
    grid = clean_grid(rows)
    qt = _toks(query)
    if not qt or len(grid) < 2:
        return None
    nc = _name_col(grid)
    best, score = None, 0.0
    for r in range(1, len(grid)):
        nt = _toks(grid[r][nc]) if nc < len(grid[r]) else set()
        if not nt:
            continue
        s = len(qt & nt) / len(qt)  # fraction of query words found in the row name
        if s > score:
            best, score = r, s
    return best if score >= 0.6 else None


def render_item(rows: list[list[str]], query: str) -> tuple[str, bool, str | None]:
    """Single-item stat card when the query names one row of the table; otherwise
    the whole table. Returns ``(block, wide, item_name)``.

    The card is a 2-column ``Stat | Value`` table — each of the item's columns
    becomes a row (the stat label on the left, its value on the right) — titled by
    the item name (returned so the caller can use it as the card header).
    ``item_name`` is None when no single row matched (whole-table fallback)."""
    grid = clean_grid(rows)
    # Component stat block (e.g. a Traveller ship): rows already *are* the stats,
    # so render col0 as the label and the rest of the row as the value — no row to
    # match against. The ship name comes from the chunk's heading, not the grid.
    if is_ship_statblock(grid):
        block, wide = _statblock_card(grid)
        if block:
            return block, wide, None
    # A single-item grid (header + one row, e.g. an exploded catalog pick) always
    # renders that item's card; otherwise match the query against the rows.
    r = 1 if len(grid) == 2 else match_row(grid, query)
    if r is None:
        block, wide = render_table(rows)
        return block, wide, None
    ncols = max(len(grid[0]), len(grid[r]))
    header = grid[0] + [""] * (ncols - len(grid[0]))
    row = grid[r] + [""] * (ncols - len(grid[r]))
    name_col = _name_col([header, row])
    name = row[name_col] if name_col < len(row) and row[name_col] else query
    pairs = [(header[j] or "—", row[j]) for j in range(ncols) if j != name_col and row[j]]
    block, wide = _stat_card(pairs)
    if not block:  # nothing but the name — fall back to the whole table
        block, wide = render_table(rows)
        return block, wide, None
    return block, wide, name
