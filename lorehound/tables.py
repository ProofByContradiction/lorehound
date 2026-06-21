"""Reconstruct and render tables pulled from RPG PDFs.

PyMuPDF renders many RPG tables as *images*; pymupdf4llm emits their OCR'd text
between "Start/End of picture text" markers with ``<br>`` between rows. Those
would otherwise be dropped, so we recover them here and render them as aligned
monospace blocks that read well in Discord.

Reconstruction is best-effort: two-column tables (most rules lookups — hit
location, fire modifiers, encounter tables) rebuild cleanly; wider OCR'd grids
can't always be split confidently, so ``render_table`` flags those as ``messy``
and the caller adds a "verify against the book" note.
"""

from __future__ import annotations

import re
import textwrap

_BR = re.compile(r"<br\s*/?>", re.I)

# A "value-like" cell: a die result, modifier, range, or dash — the codey column
# in a two-column rules table. Handles ASCII '-' and en/em dashes.
_VALUE = re.compile(r"^(?:[+\-–—]|[Dd]\d+|\d+\+|[+\-–—/\d]*\d[+\-–—/\d]*)$")


def parse_picture_rows(body: str) -> list[str]:
    """Split an OCR'd picture-text block body into cleaned row strings."""
    rows = []
    for part in _BR.split(body):
        cell = " ".join(part.split())  # collapse whitespace/newlines
        if cell:
            rows.append(cell)
    return rows


def _is_value(tok: str) -> bool:
    return bool(_VALUE.match(tok))


def _split_leading(row: str) -> tuple[str, str] | None:
    """('1', 'Legs') if the row starts with a value-like key, else None."""
    toks = row.split()
    if toks and _is_value(toks[0]):
        return toks[0], " ".join(toks[1:])
    return None


def _split_trailing(row: str) -> tuple[str, str] | None:
    """('Quick shot', '-1') if the row ends with a value-like cell, else None."""
    toks = row.split()
    if toks and _is_value(toks[-1]):
        return " ".join(toks[:-1]), toks[-1]
    return None


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
    (per-column max widths) so wide tables stay legible in Discord's container.

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


def _uniform_ncols(data: list[str]) -> int | None:
    """Return N if the data rows share a dominant token count N>=3 (a uniform
    grid), else None."""
    from collections import Counter

    counts = Counter(len(r.split()) for r in data)
    n, freq = counts.most_common(1)[0]
    if n >= 3 and freq >= max(2, int(len(data) * 0.7)):
        return n
    return None


def render_table(rows: list[str]) -> tuple[str, bool]:
    """Render row strings as a bordered monospace table.

    Returns ``(code_block, messy)``. ``messy`` is True when columns couldn't be
    confidently reconstructed; the caller adds a verify-against-book note.
    """
    rows = [r for r in rows if r]
    if not rows:
        return "```\n(empty table)\n```", True
    header, data = rows[0], rows[1:]

    if not data:
        return "```\n" + _box([[header]], ["l"], [46]) + "\n```", True

    # --- Tier 1: two-column key/value (most rules lookups) ------------------
    lead = sum(1 for r in data if _split_leading(r))
    trail = sum(1 for r in data if _split_trailing(r))
    half = len(data) / 2
    orient = "lead" if (lead >= trail and lead >= half) else (
        "trail" if trail >= half else None
    )

    if orient:
        pairs: list[tuple[str, str]] = []
        for r in data:
            sp = _split_leading(r) if orient == "lead" else _split_trailing(r)
            if sp is None:  # OCR wrap fragment: fold into the previous row
                if pairs:
                    a, b = pairs[-1]
                    pairs[-1] = (
                        (a, f"{b} {r}".strip())
                        if orient == "lead"
                        else (f"{a} {r}".strip(), b)
                    )
                else:
                    pairs.append((r, "") if orient == "lead" else ("", r))
                continue
            pairs.append(sp)

        hsp = _split_leading(header) if orient == "lead" else _split_trailing(header)
        if hsp is None:
            toks = header.split()
            if len(toks) > 1:
                hsp = (
                    (toks[0], " ".join(toks[1:]))
                    if orient == "lead"
                    else (" ".join(toks[:-1]), toks[-1])
                )
            else:
                hsp = (header, "")

        cells = [[hsp[0], hsp[1]]] + [[a, b] for a, b in pairs]
        aligns, caps = (
            (["c", "l"], [10, 30]) if orient == "lead" else (["l", "c"], [30, 10])
        )
        return (
            "```\n" + _box(cells, aligns, caps, row_seps=_wraps(cells, caps)) + "\n```",
            False,
        )

    # --- Tier 2: uniform N-column grid (skill-level / attribute tables) -----
    n = _uniform_ncols(data)
    if n:
        def split_n(s: str) -> list[str]:
            toks = s.split()
            if len(toks) <= n:
                return toks + [""] * (n - len(toks))
            return toks[: n - 1] + [" ".join(toks[n - 1:])]  # extras → last cell

        body = [split_n(r) for r in data]
        caps = [16] * n
        aligns = ["c"] + ["l"] * (n - 1)  # codes/numbers centre; text left
        head_toks = header.split()
        if len(head_toks) == n:
            cells = [head_toks] + body
            tbl = _box(cells, aligns, caps, row_seps=_wraps(cells, caps))
            return "```\n" + tbl + "\n```", False
        # Header doesn't map cleanly to N columns — show it as a caption line.
        tbl = _box(body, aligns, caps, has_header=False, row_seps=_wraps(body, caps))
        return "```\n" + header + "\n" + tbl + "\n```", False

    # --- Tier 3: complex grid — columns can't be recovered from OCR text ----
    return "```\n" + _box([[r] for r in rows], ["l"], [46]) + "\n```", True
