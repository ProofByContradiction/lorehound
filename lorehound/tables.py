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

# A die-roll column header: "D10", "2D6", "1D", "D66", "D100", or "Roll".
_DIE_HDR = re.compile(r"(?i)^(roll|\d*d\d+|\d*d)$")


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
    """Render a (wide) table as one vertical record per row — ``**Label:** value``
    pairs that wrap to the screen, so nothing overflows on mobile. Markdown (not a
    code block) so Discord reflows it to the viewport width. With a single data row
    this is the single-item gear card."""
    header = grid[0]
    ncols = len(header)
    name_col = _name_col(grid)
    blocks = []
    for r in range(1, len(grid)):
        row = grid[r]
        # Leading short "code" columns (e.g. a D10 roll) become a labeled lead line
        # ("D10 6"); the name column + the rest go in the body ("everything else").
        codes = [
            (f"{header[j]} {row[j]}".strip() if header[j] else row[j])
            for j in range(name_col)
            if row[j]
        ]
        body = []
        name = row[name_col] if name_col < len(row) else ""
        if name:
            body.append(f"**{name}**")
        body += [
            f"**{header[j]}:** {row[j]}" if header[j] else row[j]
            for j in range(name_col + 1, ncols)
            if row[j]
        ]
        if codes:  # roll/index table: dice header+number leads, everything else below
            block = "**" + " · ".join(codes) + "**"
            if body:
                block += "\n" + "  ·  ".join(body)
        else:  # name itself is the lead (e.g. a weapon/gear card)
            block = body[0] if body else "**—**"
            if body[1:]:
                block += "\n" + "  ·  ".join(body[1:])
        blocks.append(block)
    return "\n\n".join(blocks)


def _roll_key_col(grid: list[list[str]]) -> int | None:
    """Index of a leading die-roll column (header like ``D10`` / ``2D6`` / ``Roll``),
    else None. We relabel it ``Roll (Dn)`` but keep every other column."""
    head = grid[0] if grid else []
    return 0 if head and _DIE_HDR.match(head[0].strip()) else None


# Beyond this compact width even a columnar layout is unreadable, so fall back to
# vertical per-row records (a last resort, not the default).
_VERTICAL_MAX = 116


def render_table(rows: list[list[str]]) -> tuple[str, bool]:
    """Render a cell grid (first row = header) as a clean monospace table.

    Real columns are kept — a roll/damage table shows its sub-headers (Lethal,
    Effects, …) as columns rather than collapsing them — and spacing is squeezed
    (single-space gaps, internal whitespace collapsed) so wider tables still fit.
    Only genuinely huge tables fall back to vertical records.

    Returns ``(code_block, wide)`` — ``wide`` flags tables likely to scroll
    sideways on a narrow (mobile) screen.
    """
    grid = [
        [" ".join((c or "").split()) for c in r]  # strip + collapse internal whitespace
        for r in rows
        if any((c or "").strip() for c in r)
    ]
    if not grid:
        return "```\n(empty table)\n```", False
    ncols = max(len(r) for r in grid)
    grid = [r + [""] * (ncols - len(r)) for r in grid]

    # Relabel a leading die column "Roll (Dn)" but keep the rest as columns.
    roll_idx = _roll_key_col(grid)
    if roll_idx is not None:
        die = grid[0][roll_idx]
        grid[0][roll_idx] = die if die.lower().startswith("roll") else f"Roll ({die})"

    natural = [max((len(grid[r][j]) for r in range(len(grid))), default=0) for j in range(ncols)]
    compact = sum(min(n, _MAX_COL) for n in natural) + len(_GAP) * (ncols - 1)
    if ncols >= 4 and compact > _VERTICAL_MAX:
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
    grid = [[(c or "").strip() for c in r] for r in rows if any((c or "").strip() for c in r)]
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
    grid = [[(c or "").strip() for c in r] for r in rows if any((c or "").strip() for c in r)]
    r = match_row(grid, query)
    if r is None:
        block, wide = render_table(rows)
        return block, wide, None
    ncols = max(len(grid[0]), len(grid[r]))
    header = grid[0] + [""] * (ncols - len(grid[0]))
    row = grid[r] + [""] * (ncols - len(grid[r]))
    name_col = _name_col([header, row])
    name = row[name_col] if name_col < len(row) and row[name_col] else query
    stats = [["Stat", "Value"]]
    for j in range(ncols):
        if j == name_col or not row[j]:
            continue
        stats.append([header[j] or "—", row[j]])
    if len(stats) < 2:  # nothing but the name — fall back to the whole table
        block, wide = render_table(rows)
        return block, wide, None
    block, _wide = _render_grid(stats, _BUDGET)
    return block, False, name
