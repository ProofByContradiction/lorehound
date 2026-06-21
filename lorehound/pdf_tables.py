"""Structured table extraction from PDF pages (PyMuPDF).

Many RPG tables are *vector* tables with ruling lines. PyMuPDF's ``find_tables``
detects them but fragments each ruled row-band into its own table. We use the
detected bands only for their column/row *edges*, then bucket the page's
positioned words into that grid — which recovers complete tables (including
wrapped multi-line cells and shaded rows the band detector skips).

``extract_tables(page, page_no)`` returns a list of
``{"page": int, "title": str, "rows": list[list[str]]}``.
"""

from __future__ import annotations

from collections import defaultdict


def _cluster(values: set[float], tol: float) -> list[float]:
    """Collapse near-duplicate edge coordinates that are within ``tol``."""
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _interval(v: float, edges: list[float]) -> int:
    for i in range(len(edges) - 1):
        if edges[i] - 1 <= v < edges[i + 1] + 1:
            return i
    return -1


def _title(page, x0: float, y0: float, x1: float, y1: float) -> str:
    """The heading line just above the table (prefer the largest font)."""
    best = None  # (font_size, text, bottom_y)
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            lb = line["bbox"]
            cx = (lb[0] + lb[2]) / 2
            if y0 - 34 <= lb[3] <= y0 + 1 and x0 - 2 <= cx <= x1 + 2:
                text = "".join(s["text"] for s in line["spans"]).strip()
                size = max((s["size"] for s in line["spans"]), default=0.0)
                if not text:
                    continue
                if best is None or size > best[0] + 0.5 or (
                    abs(size - best[0]) <= 0.5 and lb[3] > best[2]
                ):
                    best = (size, text, lb[3])
    return best[1] if best else ""


def classify_table(chapter: str, rows: list[list[str]]) -> str:
    """Route a structured table to a lookup category from its header + chapter.

    Returns one of: 'rules' (genuine lookup table → /table), 'items' (weapon/gear
    stat blocks → /item), 'transport' (vehicle stat blocks → /transport), 'card'
    (career/archetype cards → /class), or 'noise' (junk fragment → drop).
    """
    hdr = " ".join(rows[0]).upper()
    chap = (chapter or "").upper()
    alpha_cells = sum(1 for c in rows[0] if any(ch.isalpha() for ch in c))
    if alpha_cells < 2 and len(rows) < 3:
        return "noise"
    # Weapons / gear stat blocks.
    if "ROF" in hdr or ("AMMO" in hdr and "REL" in hdr):
        return "items"
    if "DAMAGE" in hdr and "CRIT" in hdr and "BLAST" in hdr:
        return "items"
    if "CALIBER" in hdr and ("WEIGHT" in hdr or "PRICE" in hdr):
        return "items"
    if "ARMOR" in hdr and "LOCATION" in hdr:
        return "items"
    if "WEAPON" in hdr and ("DAMAGE" in hdr or "REL" in hdr):
        return "items"
    # Vehicle stat blocks.
    if "VEHICLE" in hdr or "COMBAT SPEED" in hdr:
        return "transport"
    if "FUEL" in hdr and ("ARMOR" in hdr or "SPEED" in hdr):
        return "transport"
    # Career / archetype cards (character creation).
    if hdr.startswith("CAREER") or "LAST CAREER" in hdr or "SPECIALTY (D6)" in hdr:
        return "card"
    _KNOWN = (
        "ATTRIBUTE", "SKILL LEVEL", "TARGET LEVEL", "2D6+PCS", "UNIT DURATION",
        "US SOVIET", "LEVEL DIE", "ATTRIBUTE/",
    )
    if "PLAYER CHARACTERS" in chap and len(rows[0]) >= 5 and not any(
        k in hdr for k in _KNOWN
    ):
        return "card"
    return "rules"


def extract_tables(page, page_no: int) -> list[dict]:
    raw = page.find_tables(strategy="lines").tables
    # Drop degenerate detections (empty cells make .bbox raise).
    found = []
    for t in raw:
        try:
            _ = t.bbox
        except Exception:
            continue
        if t.cells:
            found.append(t)
    if not found:
        return []

    # Bands that share a left/right x-span belong to the same column layout.
    by_col: dict[tuple, list] = defaultdict(list)
    for t in found:
        by_col[(round(t.bbox[0] / 8), round(t.bbox[2] / 8))].append(t)

    words = page.get_text("words")  # (x0, y0, x1, y1, text, block, line, word)
    out: list[dict] = []

    for bands in by_col.values():
        bands.sort(key=lambda t: t.bbox[1])
        heights = [b.bbox[3] - b.bbox[1] for b in bands]
        med_h = sorted(heights)[len(heights) // 2] if heights else 12.0
        gap_split = max(22.0, med_h * 2.0)  # only a real gap separates tables

        # Split this column layout into vertically-contiguous tables.
        groups: list[list] = [[bands[0]]]
        for t in bands[1:]:
            if t.bbox[1] - groups[-1][-1].bbox[3] > gap_split:
                groups.append([t])
            else:
                groups[-1].append(t)

        for g in groups:
            cells = [c for b in g for c in b.cells if c]
            xe = _cluster({round(c[0]) for c in cells} | {round(c[2]) for c in cells}, 6)
            ye = _cluster({round(c[1]) for c in cells} | {round(c[3]) for c in cells}, 4)
            if len(xe) < 3 or len(ye) < 2:
                continue
            nc, nr = len(xe) - 1, len(ye) - 1
            grid = [["" for _ in range(nc)] for _ in range(nr)]
            for w in words:
                cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
                if not (xe[0] - 2 <= cx <= xe[-1] + 2 and ye[0] - 2 <= cy <= ye[-1] + 2):
                    continue
                ci, ri = _interval(cx, xe), _interval(cy, ye)
                if 0 <= ci < nc and 0 <= ri < nr:
                    grid[ri][ci] = (grid[ri][ci] + " " + w[4]).strip()
            # Drop empty columns, then empty rows.
            keep = [i for i in range(nc) if any(grid[r][i].strip() for r in range(nr))]
            rows = [[row[i] for i in keep] for row in grid]
            rows = [r for r in rows if any(c.strip() for c in r)]
            if len(rows) >= 2 and len(rows[0]) >= 2:
                out.append(
                    {
                        "page": page_no,
                        "title": _title(page, xe[0], ye[0], xe[-1], ye[-1]),
                        "rows": rows,
                    }
                )
    return out


def _main() -> None:
    """Extract every page's tables from a PDF and print them as JSON.

    Run as a subprocess so table detection happens in a clean interpreter —
    importing pymupdf4llm in-process corrupts PyMuPDF's find_tables (empty cells).
    """
    import json
    import sys

    import fitz

    doc = fitz.open(sys.argv[1])
    out: list[dict] = []
    for i in range(doc.page_count):
        out.extend(extract_tables(doc[i], i + 1))
    doc.close()
    print(json.dumps(out))


if __name__ == "__main__":
    _main()
