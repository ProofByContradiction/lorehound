"""Render structured tables (cell grids) as aligned monospace blocks for Discord.

Tables are recovered upstream as real cell grids by ``pdf_tables`` (PyMuPDF
find_tables + word-bucketing), so there's no reconstruction here — just
width-aware, bordered rendering that stays legible in Discord's container.
"""

from __future__ import annotations

import textwrap


def _wrap_cell(text: str, width: int) -> list[str]:
    if not text:
        return [""]
    return textwrap.wrap(
        text, width=width, break_long_words=True, break_on_hyphens=False
    ) or [""]


def _box(
    rows_cells: list[list[str]],
    aligns: list[str],
    caps: list[int],
    has_header: bool = True,
    row_seps: bool = False,
) -> str:
    """Draw cells as a bordered monospace table, wrapping cell text to ``caps``
    (per-column max widths) so wide tables stay legible.

    ``has_header`` draws a heavier rule under the first row. ``row_seps`` draws a
    light rule between every body row — used when cells wrap, so multi-line rows
    don't read ambiguously.
    """
    ncols = max(len(r) for r in rows_cells)
    grid = [list(r) + [""] * (ncols - len(r)) for r in rows_cells]
    caps = list(caps) + [40] * (ncols - len(caps))
    wrapped = [[_wrap_cell(c, caps[i]) for i, c in enumerate(r)] for r in grid]

    widths = [0] * ncols
    for r in wrapped:
        for i, cell_lines in enumerate(r):
            widths[i] = max(widths[i], *(len(ln) for ln in cell_lines))
    widths = [min(widths[i], caps[i]) for i in range(ncols)]

    def rule(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def render(cell_lines_row: list[list[str]]) -> list[str]:
        height = max(len(c) for c in cell_lines_row)
        out = []
        for li in range(height):
            cells = []
            for i, cell_lines in enumerate(cell_lines_row):
                txt = cell_lines[li] if li < len(cell_lines) else ""
                align = aligns[i] if i < len(aligns) else "l"
                txt = txt.center(widths[i]) if align == "c" else txt.ljust(widths[i])
                cells.append(f" {txt} ")
            out.append("│" + "│".join(cells) + "│")
        return out

    lines = [rule("┌", "┬", "┐")]
    start = 0
    if has_header:
        lines += render(wrapped[0])
        lines.append(rule("├", "┼", "┤"))
        start = 1
    for idx, r in enumerate(wrapped[start:]):
        if row_seps and idx > 0:
            lines.append(rule("├", "┼", "┤"))
        lines += render(r)
    lines.append(rule("└", "┴", "┘"))
    return "\n".join(lines)


def _wraps(cells: list[list[str]], caps: list[int]) -> bool:
    """True if any cell would wrap to more than one line at the given caps."""
    return any(
        len(_wrap_cell(c, caps[i] if i < len(caps) else 40)) > 1
        for row in cells
        for i, c in enumerate(row)
    )


def render_table(rows: list[list[str]]) -> tuple[str, bool]:
    """Render a cell grid (first row = header) as a bordered monospace table.

    Returns ``(code_block, wide)`` — ``wide`` flags many-column tables that may
    still scroll horizontally on narrow screens.
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
    cap = max(6, min(22, 110 // ncols))  # tighter caps as columns grow
    caps = [cap] * ncols
    # Centre short "code" columns (die rolls, modifiers); left-align wordy ones.
    aligns = []
    for i in range(ncols):
        col = [grid[r][i] for r in range(len(grid)) if grid[r][i]]
        aligns.append("c" if col and all(len(c) <= 4 for c in col) else "l")
    block = _box(grid, aligns, caps, row_seps=_wraps(grid, caps))
    return "```\n" + block + "\n```", ncols >= 6
