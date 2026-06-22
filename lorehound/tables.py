"""Render structured tables (cell grids) as clean monospace blocks for Discord.

Tables are recovered upstream as real cell grids by ``pdf_tables`` (PyMuPDF
find_tables + word-bucketing), so there's no reconstruction here — just
width-aware rendering. The style is deliberately *borderless*: a bold header,
one underline rule, then rows with no per-cell boxes or inter-row separators,
which reads far cleaner than a full grid (especially for wordy tables). Output
is an ``ansi`` code block so the header can be emphasized.
"""

from __future__ import annotations

import textwrap

_ESC = "\x1b"
_HEAD = f"{_ESC}[1;36m"  # bold cyan header row
_RESET = f"{_ESC}[0m"
_GAP = "   "             # column separator (3 spaces)
# Prefer single-line rows: a generous width ceiling so cells wrap only when a table
# is genuinely too wide. Discord code blocks scroll horizontally, so wide-but-
# single-line beats narrow-but-wrapped for legibility.
_BUDGET = 100
_MIN_COL = 3
_MAX_COL = 40           # a single column only wraps past this many characters
_REPEAT_MIN = 25        # tables longer than this re-print the header as you scroll
_REPEAT_EVERY = 18      # …every this many body rows (pseudo-sticky header)
_MOBILE_GRID_MAX = 42   # wider 3+ column tables render as vertical records (mobile-safe)


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


def _render_vertical(grid: list[list[str]]) -> str:
    """Render a wide table as one vertical record per row — ``**Label:** value``
    pairs that wrap to the screen, so nothing overflows on mobile. Markdown (not a
    code block) so Discord reflows it to the viewport width."""
    header = grid[0]
    ncols = len(header)
    # The "name" column: first column with wordy values; short leading code columns
    # (a roll/index) fold into the record's bold lead line.
    name_col = 0
    for j in range(ncols):
        vals = [grid[r][j] for r in range(1, len(grid)) if grid[r][j]]
        if vals and sum(len(v) for v in vals) / len(vals) > 4:
            name_col = j
            break
    blocks = []
    for r in range(1, len(grid)):
        row = grid[r]
        lead = " · ".join(c for c in row[: name_col + 1] if c) or "—"
        fields = [
            f"**{header[j]}:** {row[j]}" if header[j] else row[j]
            for j in range(name_col + 1, ncols)
            if row[j]
        ]
        block = f"**{lead}**"
        if fields:
            block += "\n" + "  ·  ".join(fields)
        blocks.append(block)
    return "\n\n".join(blocks)


def render_table(rows: list[list[str]]) -> tuple[str, bool]:
    """Render a cell grid (first row = header) as a clean monospace table.

    Returns ``(code_block, wide)`` — ``wide`` flags tables likely to scroll
    sideways on a narrow (mobile) screen.
    """
    grid = [
        [(c or "").strip() for c in r]
        for r in rows
        if any((c or "").strip() for c in r)
    ]
    if not grid:
        return "```\n(empty table)\n```", False
    ncols = max(len(r) for r in grid)
    grid = [r + [""] * (ncols - len(r)) for r in grid]

    # Mobile can't horizontally scroll a code block (the swipe is hijacked, and wide
    # tables just squish), so a wide 3+ column table renders as vertical per-row
    # records that wrap to the screen instead of overflowing.
    natural = [max((len(grid[r][j]) for r in range(len(grid))), default=0) for j in range(ncols)]
    if ncols >= 3 and sum(natural) + len(_GAP) * (ncols - 1) > _MOBILE_GRID_MAX:
        return _render_vertical(grid), False

    nat = [
        max(_MIN_COL, min(_MAX_COL, max((len(grid[r][i]) for r in range(len(grid))), default=0)))
        for i in range(ncols)
    ]
    gap = len(_GAP) * (ncols - 1)
    widths = _fit_widths(nat, max(_BUDGET - gap, ncols * _MIN_COL))

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
