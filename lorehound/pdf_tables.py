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

import re
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

    def has(*words: str) -> bool:  # whole-word match — "ROF" must not hit "pROFessor"
        return all(re.search(rf"\b{w}\b", hdr) for w in words)

    def some(*words: str) -> bool:
        return any(re.search(rf"\b{w}\b", hdr) for w in words)

    # Career / archetype cards FIRST: a 'PROFESSOR' career column must not be read
    # as a weapon via the 'ROF' substring.
    if hdr.startswith("CAREER") or has("LAST", "CAREER") or "SPECIALTY (D6)" in hdr:
        return "card"
    # Weapons / gear stat blocks.
    if has("ROF") or has("AMMO", "REL"):
        return "items"
    if has("DAMAGE", "CRIT", "BLAST"):
        return "items"
    if has("CALIBER") and some("WEIGHT", "PRICE"):
        return "items"
    if some("ARMOR", "ARMOUR") and has("LOCATION"):
        return "items"
    if has("WEAPON") and some("DAMAGE", "REL"):
        return "items"
    # Vehicle stat blocks.
    if has("VEHICLE") or "COMBAT SPEED" in hdr:
        return "transport"
    if has("FUEL") and some("ARMOR", "ARMOUR", "SPEED"):
        return "transport"
    _KNOWN = (
        "ATTRIBUTE", "SKILL LEVEL", "TARGET LEVEL", "2D6+PCS", "UNIT DURATION",
        "US SOVIET", "LEVEL DIE", "ATTRIBUTE/",
    )
    if "PLAYER CHARACTERS" in chap and len(rows[0]) >= 5 and not any(
        k in hdr for k in _KNOWN
    ):
        return "card"
    return "rules"


# --- Geometric career-grid reconstruction ----------------------------------
#
# Some character-creation career tables (the T2K *military* careers) have
# inconsistent ruling lines, so ``find_tables`` shatters them into per-row bands
# and the CAREER/REQUIREMENTS/RANK/SKILLS rows leak into the page text as
# delimiter-less prose. They reconstruct cleanly from geometry, though: the
# columns sit at stable X positions (one per career, derived from the header
# row) and every row is delimited by its first-column label. We bucket each word
# into (row-band by Y, column by X) — independent of ruling lines.

_CAREER_HDR = "career"
# First-column row labels that mark a career table (a 2nd word like RANK in
# "STARTING RANK" sits right of the label column, so matching the lead word is
# enough). System-specific label sets plug in here.
_CAREER_FIELDS = (
    "requirements", "starting", "rank", "skills",
    "specialty", "specialities", "specialties",
)


def _col_starts(xs: list[float], gap: float = 34.0) -> list[float]:
    """Left edges of the columns: a new column begins wherever a sorted X-start
    jumps more than ``gap`` past the previous column's start."""
    starts: list[float] = []
    for x in xs:
        if not starts or x - starts[-1] > gap:
            starts.append(x)
    return starts


def _career_grids(page, page_no: int) -> list[dict]:
    """Reconstruct character-creation career tables from word geometry.

    A page can stack several career tables (plus prose between them), so we
    segment by ``CAREER`` header: each header starts a table that runs down
    through its first-column field labels until the next header. Returns one grid
    per reconstructed table (possibly empty)."""
    words = [w for w in page.get_text("words") if w[4].strip()]
    if not words:
        return []

    def label_of(w) -> str | None:
        t = w[4].strip().lower().strip(":")
        if t == _CAREER_HDR:
            return "career"
        return t if t in _CAREER_FIELDS else None

    tagged = [(w, label_of(w)) for w in words if label_of(w)]
    if not tagged:
        return []
    label_x = min(w[0] for w, _ in tagged)
    labels = [(w, l) for w, l in tagged if abs(w[0] - label_x) <= 12]
    header_ys = sorted(w[1] for w, l in labels if l == "career")
    if not header_ys:
        return []

    grids: list[dict] = []
    for hi, career_y in enumerate(header_ys):
        seg_end = header_ys[hi + 1] if hi + 1 < len(header_ys) else page.rect.height
        # Field-label anchors within this header's segment.
        anchors: list[float] = []
        for y in sorted(round(w[1]) for w, _ in labels if career_y - 1 <= w[1] < seg_end):
            if not anchors or y - anchors[-1] > 6:
                anchors.append(y)
        if len({l for w, l in labels if career_y - 1 <= w[1] < seg_end} - {"career"}) < 2:
            continue  # not enough field rows to be a career table
        # Specialty roll rows (1..9 in the label column, below the SPECIALTY label).
        spec_ys = [w[1] for w, l in labels if l.startswith("special") and career_y <= w[1] < seg_end]
        if spec_ys:
            spec_y = min(spec_ys)
            for w in words:
                t = w[4].strip()
                if (
                    t.isdigit() and 1 <= int(t) <= 9
                    and abs(w[0] - label_x) <= 12
                    and spec_y + 2 < w[1] < seg_end
                ):
                    y = round(w[1])
                    if all(abs(y - a) > 6 for a in anchors):
                        anchors.append(y)
            anchors.sort()

        nxt = min((a for a in anchors if a > career_y + 3), default=career_y + 20)
        hdr_words = [w for w in words if career_y - 2 <= w[1] < nxt - 2 and w[0] >= label_x - 4]
        cols = _col_starts(sorted(w[0] for w in hdr_words))
        if len(cols) < 3:
            continue  # need a label column + at least two careers

        def col_of(x: float, _cols=cols) -> int:
            c = 0
            for i, cx in enumerate(_cols):
                if x >= cx - 6:
                    c = i
            return c

        grid: list[list[str]] = []
        for i, top in enumerate(anchors):
            bottom = anchors[i + 1] if i + 1 < len(anchors) else min(top + 19, seg_end)
            buckets: list[list] = [[] for _ in cols]
            for w in words:
                if top - 3 <= w[1] < bottom - 3 and w[0] >= label_x - 6:
                    buckets[col_of(w[0])].append(w)
            row = [
                " ".join(x[4] for x in sorted(b, key=lambda w: (round(w[1] / 3), w[0])))
                for b in buckets
            ]
            if any(row):
                grid.append(row)
        if len(grid) >= 3 and len(grid[0]) >= 3:
            grids.append({"page": page_no, "title": "CAREER", "rows": grid})
    return grids


def _has_clean_career_card(tables: list[dict]) -> bool:
    """True if ``find_tables`` already captured a proper career card here (header
    row leads with CAREER). The geometric reconstructor is only a *fallback* for
    pages where it didn't — running it on cleanly-detected pages mangles names."""
    for t in tables:
        rows = t.get("rows") or []
        if len(rows) >= 3 and rows[0] and rows[0][0].strip().upper().startswith("CAREER"):
            return True
    return False


def extract_tables(page, page_no: int, profile=None) -> list[dict]:
    """Generic table extraction for one page, plus any source-specific
    reconstructions from ``profile`` (the hybrid indexer — see :mod:`sources`)."""
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
    # Source-specific geometric reconstructions (career grids, gear cards, ship
    # blocks) for layouts the generic pass can't recover; baseline-only otherwise.
    if profile is not None:
        out.extend(profile.reconstruct(page, page_no, out))
    return out


def _t2k_careers(page, page_no, existing) -> list[dict]:
    """T2K career reconstructor: rebuild column career-cards geometrically, but
    only where find_tables didn't already capture a clean one (fallback)."""
    if _has_clean_career_card(existing):
        return []
    return _career_grids(page, page_no)


# Career-page section markers — a Mongoose Traveller career spread carries these.
_TRAV_CAREER_MARKERS = ("Qualification", "Survival", "Advancement", "Mishaps",
                        "Events", "Skills and", "Ranks and", "Mustering")


def _traveller_careers(page, page_no, existing) -> list[dict]:
    """Traveller career *anchor*: Mongoose careers are heading-anchored (a big
    career name over a 2-page spread of sub-tables), and the name often doesn't
    survive into the Markdown. We detect the name from page geometry and emit a
    one-per-career anchor card ``[[CAREER, name], [PAGE, n]]``; careers.py then
    assembles each career from the skill/rank tables on its page range."""
    text = page.get_text()
    if sum(m in text for m in _TRAV_CAREER_MARKERS) < 2:
        return []  # not a career spread (skip sourcebook/setting pages)
    for b in page.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            txt = " ".join(s["text"] for s in spans).strip()
            size = max((s["size"] for s in spans), default=0.0)
            low = txt.lower()
            if (
                26 <= size <= 40
                and 2 <= len(txt) <= 22
                and txt.replace(" ", "").isalpha()
                and not any(w in low for w in ("traveller", "creation", "skills", "tasks"))
            ):
                return [{
                    "page": page_no,
                    "title": txt.title(),
                    "rows": [["CAREER", txt.title()], ["PAGE", str(page_no)]],
                }]
    return []


# --- Source profiles (hybrid indexer; see lorehound/sources.py) -------------

from . import sources  # noqa: E402

sources.register(
    sources.SourceProfile(
        name="Twilight 2000 (4E)",
        games=("twilight", "t2k", "2000"),
        reconstructors=[_t2k_careers],
    )
)
sources.register(
    sources.SourceProfile(
        name="Traveller (Mongoose)",
        games=("traveller",),
        reconstructors=[_traveller_careers],
    )
)


def _main() -> None:
    """Extract every page's tables from a PDF and print them as JSON.

    Run as a subprocess so table detection happens in a clean interpreter —
    importing pymupdf4llm in-process corrupts PyMuPDF's find_tables (empty cells).
    PyMuPDF also prints chatter to stdout (e.g. the "pymupdf_layout" hint); we
    silence stdout at the fd level during extraction so ONLY our JSON reaches the
    parent, which does ``json.loads(stdout)``.

    Argv: ``<pdf-path> [game]`` — ``game`` selects a source profile.
    """
    import json
    import os
    import sys

    import fitz

    doc = fitz.open(sys.argv[1])
    game = sys.argv[2] if len(sys.argv) > 2 else ""
    profile = sources.profile_for(game)
    saved_fd = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    try:
        out: list[dict] = []
        for i in range(doc.page_count):
            out.extend(extract_tables(doc[i], i + 1, profile))
    finally:
        sys.stdout.flush()
        os.dup2(saved_fd, 1)  # restore real stdout for the JSON
        os.close(devnull)
        os.close(saved_fd)
    doc.close()
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()


if __name__ == "__main__":
    _main()
