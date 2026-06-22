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

    lines = [f"{_HEAD}{ln}{_RESET}" for ln in fmt_row(0)]  # bold header (may wrap)
    lines.append(_GAP.join("─" * widths[i] for i in range(ncols)))
    for r in range(1, len(grid)):
        lines += fmt_row(r)

    wide = ncols >= 6 or (sum(widths) + gap) > 58
    return "```ansi\n" + "\n".join(lines) + "\n```", wide
