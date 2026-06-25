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


def _parse_contents_page(doc) -> list[tuple[str, int]]:
    """Find a printed Contents page and parse it into (CHAPTER TITLE, printed_page)
    pairs. RPG PDFs often lack usable bookmarks but have a visual ToC page."""
    for pi in range(min(14, doc.page_count)):
        lines = [l.strip() for l in doc.load_page(pi).get_text().splitlines() if l.strip()]
        idx = next((i for i, l in enumerate(lines) if l.upper() == "CONTENTS"), None)
        if idx is None:
            continue
        pairs: list[tuple[str, int]] = []
        title = None
        for l in lines[idx + 1:]:
            if re.fullmatch(r"\d{1,3}", l):
                if title:
                    pairs.append((title, int(l)))
                    title = None
            elif l.isupper() and len(l) > 3 and any(c.isalpha() for c in l):
                title = l
        if len(pairs) >= 4:
            return pairs
    return []


def toc_from_contents_page(doc) -> list[tuple[int, str, int]]:
    """Synthesize a ``get_toc()``-style [(level, title, page)] list from the printed
    Contents page when the PDF has no usable bookmarks. The printed page numbers are
    mapped to PDF pages via a single offset (printed→PDF is constant within a book),
    derived by locating a few chapter titles as large headings. Returns 1-based pages."""
    pairs = _parse_contents_page(doc)
    if len(pairs) < 4:
        return []
    head_page: dict[str, int] = {}
    for pi in range(doc.page_count):
        try:
            blocks = doc.load_page(pi).get_text("dict")["blocks"]
        except Exception:
            continue
        for b in blocks:
            for ln in b.get("lines", []):
                spans = ln.get("spans", [])
                txt = " ".join(s["text"] for s in spans).strip().upper()
                if len(txt) > 3 and max((s["size"] for s in spans), default=0) >= 18 and txt not in head_page:
                    head_page[txt] = pi
    offsets = sorted(head_page[t.upper()] - p for t, p in pairs if t.upper() in head_page)
    if not offsets:
        return []
    offset = offsets[len(offsets) // 2]  # median (robust to a mislocated title)
    return [(1, title, printed + offset + 1) for title, printed in pairs]


def classify_table(chapter: str, rows: list[list[str]], profile=None) -> str:
    """Route a structured table to a lookup category from its header + chapter.

    Returns one of: 'rules' (genuine lookup table → /table), 'items' (weapon/gear
    stat blocks → /item), 'transport' (vehicle stat blocks → /transport), 'card'
    (career/archetype cards → /class), or 'noise' (junk fragment → drop).

    ``profile`` (a :class:`sources.SourceProfile`) supplies the per-system
    chapter-fallback routing — its ``item_chapters`` / ``transport_chapters`` — so
    routing knowledge lives on the source registry, not in this generic function.
    With no profile, a header-less table falls through to 'rules'.
    """
    hdr = " ".join(rows[0]).upper()
    chap = (chapter or "").upper()

    # A full component stat block (first column Hull / Armour / M-Drive…) is a
    # vehicle/ship wherever it sits — route it to /transport regardless of chapter
    # (e.g. example ships in sourcebook chapters like "Exploration"). Construction
    # option tables don't match (their rows are options, not systems). Checked
    # before the noise guard so a compact block isn't dropped on the alpha floor.
    from .tables import is_ship_statblock

    if is_ship_statblock(rows):
        return "transport"

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
    # Vehicle stat blocks BEFORE weapons: a vehicle table carries a MAIN WEAPON +
    # REL column, so it would otherwise be mis-read as a weapon catalogue.
    if has("VEHICLE") or "COMBAT SPEED" in hdr:
        return "transport"
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
    # Chapter fallback: when the header gives no signal, a clean single-domain
    # chapter (e.g. Traveller's Contents-derived "EQUIPMENT" / "VEHICLES") routes
    # the table. The chapter sets come from the source profile (not hardcoded here),
    # and it's an exact match only — so a mixed chapter like T2K's "Weapons,
    # Vehicles & Gear" doesn't trip it (its tables already routed by header keywords
    # above), and an unprofiled book never force-routes on chapter alone.
    if profile and chap in profile.item_chapters:
        return "items"
    if profile and chap in profile.transport_chapters:
        return "transport"
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

# Known Mongoose 2e core careers — the ~30pt heading word that names a career
# spread. Used to recognise a career page (and reject e.g. the "S KILLS AND
# TASKS" chapter banner, which is large but not a career name).
_TRAV_CORE_CAREERS = frozenset({
    "agent", "army", "citizen", "drifter", "entertainer", "marine", "merchant",
    "navy", "noble", "rogue", "scholar", "scout", "prisoner", "psion",
})

# The universal Mongoose 2e Table-A skill columns. They appear in the PDF text on
# most careers (as an uppercase ``1D | PERSONAL DEVELOPMENT | …`` header), but a few
# omit them and many split the header across two lines — so we always normalise
# Table A to these fixed names. The 5th column ("Officer") appears only on the
# careers with a commissioned track (Navy / Army / Marine).
_TRAV_SKILLS_A_HDR = ("Roll", "Personal Development", "Service Skills",
                      "Advanced Education", "Officer")

_TRAV_NUM = re.compile(r"\d{1,2}$")
_TRAV_UPPER = re.compile(r"^[A-Z][A-Z0-9 &/().,+-]*$")


def _trav_y_bands(words, tol: float = 4.0) -> list[float]:
    """Sorted row-band tops: y-coordinates collapsed so words on the same text
    line share a band (a new band starts when y jumps more than ``tol``)."""
    bands: list[float] = []
    for y in sorted(round(w[1]) for w in words):
        if not bands or y - bands[-1] > tol:
            bands.append(y)
    return bands


def _trav_col_anchors(words, gap: float = 30.0, numeric_only: bool = True) -> list[float]:
    """Left edges of the columns in a rectangular band, robust to wrapped cells.

    Per row we take the leftmost word of each visual cluster (words within ``gap``
    of each other are one cell — so ``Gun Combat`` / ``Electronics (comms)`` stay a
    single cell, not two columns). We then cluster those starts across rows and
    keep only the columns that recur in at least half the rows, which discards the
    phantom column a single wrapped continuation would otherwise mint.

    ``numeric_only`` restricts the anchor rows to those whose first column is a
    roll index (1..12) — the clean, compact data rows — and ignores the wide
    multi-word header/heading rows. Off for name-keyed tables (career progress)."""
    bands = _trav_y_bands(words)
    starts_per_row: list[list[float]] = []
    roll_xs: list[float] = []
    for bi, top in enumerate(bands):
        bottom = bands[bi + 1] if bi + 1 < len(bands) else top + 100
        row = sorted((w for w in words if top - 4 < w[1] < bottom - 4), key=lambda w: w[0])
        if not row:
            continue
        if numeric_only:
            if not _TRAV_NUM.fullmatch(row[0][4].strip()):
                continue
            # The roll index is its own (short, far-left) column even when the next
            # column sits closer than ``gap`` (Navy's PD column starts ~20pt right
            # of the roll). Seed col0 at the roll's x; cluster the rest beyond it.
            roll_xs.append(row[0][0])
            data = [w for w in row[1:] if w[0] > row[0][0] + 14]
        else:
            data = row
        prev = None
        rs: list[float] = []
        for w in data:
            if prev is None or w[0] - prev > gap:
                rs.append(w[0])
            prev = w[0]
        starts_per_row.append(rs)
    flat = sorted(x for rs in starts_per_row for x in rs)
    clusters: list[list[float]] = []  # [rep_x, count]
    for x in flat:
        if clusters and x - clusters[-1][0] <= gap:
            clusters[-1][1] += 1
        else:
            clusters.append([x, 1])
    thresh = max(2, len(starts_per_row) // 2)
    cols = [cx for cx, cnt in clusters if cnt >= thresh]
    if roll_xs:  # prepend the roll column anchor (median roll x)
        cols = [sorted(roll_xs)[len(roll_xs) // 2]] + cols
    return cols


def _trav_band_rows(words, x_lo, x_hi, y_lo, y_hi, starts, tol: float = 4.0) -> list[list[str]]:
    """Bucket the words in a rectangle into a cell grid using fixed column
    ``starts`` (each word joins the rightmost start at/left of it) and y row-bands."""
    sel = [w for w in words if x_lo <= w[0] < x_hi and y_lo <= w[1] < y_hi and w[4].strip()]
    if not sel or not starts:
        return []

    def col_of(x: float) -> int:
        c = 0
        for i, cx in enumerate(starts):
            if x >= cx - 6:
                c = i
        return c

    bands = _trav_y_bands(sel)
    rows: list[list[str]] = []
    for bi, top in enumerate(bands):
        bottom = bands[bi + 1] if bi + 1 < len(bands) else top + 100
        cells: list[list] = [[] for _ in starts]
        for w in sel:
            if top - tol < w[1] < bottom - tol:
                cells[col_of(w[0])].append(w)
        rows.append([" ".join(x[4] for x in sorted(b, key=lambda w: w[0])) for b in cells])
    return rows


def _trav_drop_empty_cols(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    nc = max(len(r) for r in rows)
    rows = [r + [""] * (nc - len(r)) for r in rows]
    keep = [i for i in range(nc) if any(r[i].strip() for r in rows)]
    return [[r[i] for i in keep] for r in rows]


def _trav_merge_headerless_cols(rows: list[list[str]]) -> list[list[str]]:
    """Fold any column whose header cell is blank into the column on its left,
    cell by cell. A wrapped cell ("Melee (unarmed)") can split its continuation
    ("(unarmed)") into a phantom column the geometry pass over-segments; since every
    real Mongoose career column carries a header, a header-less column is that
    spill-over and belongs back with its neighbour."""
    if len(rows) < 2 or not rows[0]:
        return rows
    header = rows[0]
    keep = [0] + [j for j in range(1, len(header)) if header[j].strip()]
    if len(keep) == len(header):
        return rows
    out: list[list[str]] = []
    for r in rows:
        r = r + [""] * (len(header) - len(r))
        merged = []
        for j in range(len(header)):
            if j in keep:
                merged.append(r[j])
            elif merged:  # append spill-over to the last kept column
                merged[-1] = (merged[-1] + " " + r[j]).strip() if r[j] else merged[-1]
        out.append(merged)
    return out


def _trav_is_upper(cell: str) -> bool:
    c = cell.strip()
    return bool(c) and bool(_TRAV_UPPER.match(c)) and any(ch.isalpha() for ch in c)


def _trav_section_headings(page) -> list[tuple[str, float, float, float]]:
    """(text, size, x0, y0) for every line on the page (heading detection by the
    caller). Multi-span lines are joined; size is the line's max span size."""
    out: list[tuple[str, float, float, float]] = []
    for b in page.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            txt = " ".join(s["text"] for s in spans).strip()
            size = max((s["size"] for s in spans), default=0.0)
            if txt:
                out.append((txt, size, ln["bbox"][0], ln["bbox"][1]))
    return out


def _trav_heading_rects(page, min_size: float = 13.0) -> list[tuple[float, float, float, float]]:
    """Bounding boxes of heading-sized text lines (>= ``min_size`` pt). Their words
    (the big career name and the vertical section titles like "Ranks and bonuses"
    set in the table's left margin) must be dropped from the data grids, where they
    would otherwise leak into a column."""
    rects: list[tuple[float, float, float, float]] = []
    for b in page.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            size = max((s["size"] for s in spans), default=0.0)
            txt = " ".join(s["text"] for s in spans).strip()
            if txt and size >= min_size:
                rects.append(tuple(ln["bbox"]))
    return rects


def _trav_words(page) -> list:
    """Page words with heading-sized text (career name, vertical section titles)
    removed, so they can't pollute a reconstructed data column."""
    rects = _trav_heading_rects(page)

    def in_heading(w) -> bool:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        return any(x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1
                   for x0, y0, x1, y1 in rects)

    return [w for w in page.get_text("words") if w[4].strip() and not in_heading(w)]


def _trav_skills_sections(words, page_no, y_lo, y_hi) -> list[dict]:
    """Reconstruct the "Skills and training" band into Table A (the universal
    Roll | Personal Development | Service Skills | Advanced Education grid) and, if
    present, Table B (the per-assignment specialist-skills grid). The two stack in
    one band; a second ``1D``-led uppercase row marks B's header, splitting them."""
    band = [w for w in words if y_lo <= w[1] < y_hi]
    starts = _trav_col_anchors(band, gap=30.0, numeric_only=True)
    rows = _trav_band_rows(words, 82, 560, y_lo, y_hi, starts)
    # Header rows: first cell "1D" with >=2 uppercase cells after it. The 1st is
    # Table A's header (universal skills), the 2nd starts Table B (specialists).
    hdr_idx = [
        i for i, r in enumerate(rows)
        if r and r[0].strip() == "1D" and sum(_trav_is_upper(c) for c in r[1:]) >= 2
    ]
    if not hdr_idx:
        return []
    out: list[dict] = []
    a_i = hdr_idx[0]
    b_i = hdr_idx[1] if len(hdr_idx) > 1 else None
    a_end = b_i if b_i is not None else len(rows)
    a_rows = [r for r in rows[a_i + 1:a_end] if r and _TRAV_NUM.fullmatch(r[0].strip())]
    a = _trav_drop_empty_cols([rows[a_i]] + a_rows)
    if a:  # normalise A's header to the universal column names
        n = len(a[0])
        a[0] = (list(_TRAV_SKILLS_A_HDR) + a[0][len(_TRAV_SKILLS_A_HDR):])[:n]
    if len(a) >= 2:
        out.append({"page": page_no, "title": "Skills and training", "rows": a})
    if b_i is not None:
        b_rows = [r for r in rows[b_i + 1:] if r and _TRAV_NUM.fullmatch(r[0].strip())]
        b = _trav_merge_headerless_cols(
            _trav_drop_empty_cols([["Roll"] + rows[b_i][1:]] + b_rows)
        )
        if len(b) >= 2:
            out.append({"page": page_no, "title": "Specialist Skills", "rows": b})
    return out


def _trav_ranks_section(words, page_no, y_lo, y_hi) -> list[dict]:
    """Reconstruct the "Ranks and bonuses" band: RANK | <category> | SKILL OR BONUS.
    A career can stack two rank tracks (e.g. Agent's enlisted vs intelligence), each
    with its own ``RANK``-led header; we keep them as one table (the inline second
    header reads fine as a sub-divider)."""
    band = [w for w in words if y_lo <= w[1] < y_hi]
    starts = _trav_col_anchors(band, gap=30.0, numeric_only=True)
    rows = _trav_band_rows(words, 82, 560, y_lo, y_hi, starts)
    rows = [r for r in rows if any(c.strip() for c in r)]
    rows = _trav_drop_empty_cols(rows)
    # Drop a leading orphan row that isn't the RANK header or a numbered rank.
    while rows and not (
        rows[0][0].strip().upper().startswith("RANK")
        or _TRAV_NUM.fullmatch(rows[0][0].strip())
    ):
        rows.pop(0)
    # Some careers render the RANK header twice (an uppercase + a title-case shadow
    # line); drop a header row that immediately repeats the one above it.
    deduped: list[list[str]] = []
    for r in rows:
        if (
            deduped
            and r[0].strip().upper().startswith("RANK")
            and deduped[-1][0].strip().upper().startswith("RANK")
        ):
            continue
        if (
            deduped
            and deduped[-1][0].strip().upper().startswith("RANK")
            and [c.strip().lower() for c in r] == [c.strip().lower() for c in deduped[-1]]
        ):
            continue
        deduped.append(r)
    rows = deduped
    if len(rows) < 3 or len(rows[0]) < 2:
        return []
    return [{"page": page_no, "title": "Ranks and bonuses", "rows": rows}]


def _trav_progress_section(words, page_no, y_lo, y_hi) -> list[dict]:
    """Reconstruct the "Career progress" survival/advancement table: a blank-headed
    assignment column then SURVIVAL and ADVANCEMENT (their checks per assignment)."""
    band = [w for w in words if 310 <= w[0] < 565 and y_lo <= w[1] < y_hi]
    starts = _trav_col_anchors(band, gap=30.0, numeric_only=False)
    rows = _trav_drop_empty_cols(_trav_band_rows(words, 310, 565, y_lo, y_hi, starts))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2 or len(rows[0]) < 2:
        return []
    if not rows[0][0].strip():  # name the blank assignment-column header
        rows[0][0] = "Assignment"
    return [{"page": page_no, "title": "Career progress", "rows": rows}]


def _trav_mustering_section(words, page_no, y_lo, y_hi) -> list[dict]:
    """Reconstruct "Mustering out benefits": 1D | CASH | BENEFITS."""
    band = [w for w in words if 310 <= w[0] < 565 and y_lo <= w[1] < y_hi]
    starts = _trav_col_anchors(band, gap=30.0, numeric_only=True)
    rows = _trav_drop_empty_cols(_trav_band_rows(words, 310, 565, y_lo, y_hi, starts))
    rows = [r for r in rows if any(c.strip() for c in r)]
    rows = _trav_merge_headerless_cols(rows)  # fold a wrapped-benefit spill column
    if len(rows) < 3 or len(rows[0]) < 2:
        return []
    return [{"page": page_no, "title": "Mustering out benefits", "rows": rows}]


# Tokens that aren't part of a Mishaps/Events description: the small-caps column
# labels printed above each table, and a stray "table"/page reference.
_TRAV_ROLL_NOISE = frozenset({"MISHAP", "EVENT", "MISHAPS", "EVENTS"})


def _trav_roll_text_section(words, page_no, title, header, y_lo, y_hi, page_h=792.0) -> list[dict]:
    """Reconstruct a roll | description table (Mishaps / Events), where each
    description wraps over several text lines. Rows are keyed by the roll-index word
    in the left column; the description gathers every body word down to the next
    roll index. The roll column's x varies a little per career (~79–86), so we
    locate it from the leftmost numeric words rather than hardcoding it."""
    foot = page_h - 24  # drop the page-number folio in the bottom margin

    def keep(w) -> bool:
        return (
            70 <= w[0] < 580 and y_lo <= w[1] < min(y_hi, foot)
            and w[4].strip() and w[4].strip().upper() not in _TRAV_ROLL_NOISE
        )

    sel = [w for w in words if keep(w)]
    nums = [w for w in sel if w[0] < 110 and _TRAV_NUM.fullmatch(w[4].strip())]
    if not nums:
        return []
    roll_x = min(w[0] for w in nums)               # the roll column's left edge
    rolls = [w for w in nums if w[0] <= roll_x + 12]
    body_x = roll_x + 22                            # descriptions sit ~25pt right
    rolls.sort(key=lambda w: w[1])
    rows = [list(header)]
    for i, rw in enumerate(rolls):
        top = rw[1] - 2
        bottom = rolls[i + 1][1] - 2 if i + 1 < len(rolls) else min(y_hi, foot)
        body = [w for w in sel if w[0] >= body_x and top <= w[1] < bottom]
        body.sort(key=lambda w: (round(w[1] / 3), w[0]))
        text = " ".join(w[4] for w in body).strip()
        if text:
            rows.append([rw[4].strip(), text])
    if len(rows) < 3:
        return []
    return [{"page": page_no, "title": title, "rows": rows}]


def _trav_has_section_heading(page, *names, min_size: float = 18.0) -> bool:
    """True if the page has a styled heading (>= ``min_size`` pt) whose text starts
    with one of ``names`` (case-insensitive) — distinguishes a real career section
    heading from an incidental prose mention of the word."""
    for txt, size, _x, _y in _trav_section_headings(page):
        low = txt.strip().lower()
        if size >= min_size and any(low.startswith(n) for n in names):
            return True
    return False


def is_traveller_career_page(page) -> bool:
    """True if this page is part of a Mongoose Traveller career spread — either the
    career page (a ~30pt known-career-name heading) or its facing page (the Mishaps
    *and* Events section headings). Used to swap the mangled generic tables for the
    clean geometric reconstruction. The strong, specific signals (a known career
    name; both the Mishaps and Events headings) keep this off non-career pages — a
    sourcebook page that merely mentions "events" in prose won't trip it."""
    if _trav_career_name(page):
        return True
    # A career's facing page: the Mishaps and Events tables (their styled headings).
    return (
        _trav_has_section_heading(page, "mishaps")
        and _trav_has_section_heading(page, "events")
    )


def _trav_career_name(page) -> str:
    """The career name from the page's ~30pt heading word, Title-cased — '' if this
    page has no career-name heading (e.g. the facing Mishaps/Events page)."""
    for txt, size, _x, _y in _trav_section_headings(page):
        low = txt.lower().replace(" ", "")
        if 26 <= size <= 40 and 2 <= len(txt) <= 22 and low in _TRAV_CORE_CAREERS:
            return txt.title()
    return ""


def traveller_career_sections(page, page_no: int) -> list[dict]:
    """Geometrically reconstruct every career sub-table on one Traveller career-spread
    page, returning clean ``{"page", "title", "rows"}`` dicts with proper headers.

    ``find_tables(strategy="lines")`` shatters these tables — it drops the 4th skill
    column, omits header rows, and merges Table A's last row into Table B's header —
    so the generic pass mangles them. Here we cluster columns from word x-positions
    (recurrence-filtered so wrapped cells don't mint phantom columns) and attach the
    correct header to each section. Sections are carved from the page's own headings:

    * career page — Skills and training (+ Specialist Skills), Ranks and bonuses,
      Career progress, Mustering out benefits;
    * facing page — Mishaps, Events.

    Returns ``[]`` for a non-career page, so callers can guard cheaply."""
    if not is_traveller_career_page(page):
        return []
    words = _trav_words(page)  # heading-sized words removed (career name, margins)
    if not words:
        return []
    heads = _trav_section_headings(page)
    page_bottom = page.rect.height

    def heading_y(*names, x_lo=0.0, x_hi=1e9):
        for txt, _size, x, y in heads:
            t = txt.strip().lower()
            if any(t == n or t.startswith(n) for n in names) and x_lo <= x <= x_hi:
                return y
        return None

    out: list[dict] = []

    # --- left-page sections (Skills, Ranks): full width, below the right column.
    # A section's band ends at the next left-page heading of ANY kind (Skills,
    # Ranks, Mishaps, Events) so it can't swallow the following section's content.
    skills_y = heading_y("skills and training", x_hi=300)
    ranks_y = heading_y("ranks and bonuses", x_hi=400)
    mish_y = heading_y("mishaps", x_hi=300)
    ev_y = heading_y("events", x_hi=300)
    left_heads = sorted(y for y in (skills_y, ranks_y, mish_y, ev_y) if y is not None)

    def left_end(start):
        return min((y for y in left_heads if y > start + 2), default=page_bottom) - 2

    if skills_y is not None:
        # The (uppercase) Table-A header sits a little ABOVE the vertical heading's
        # top, so start the band above it; end at the next left-page heading.
        out += _trav_skills_sections(words, page_no, skills_y - 22, left_end(skills_y))
    if ranks_y is not None:
        out += _trav_ranks_section(words, page_no, ranks_y - 22, left_end(ranks_y))

    # --- right-column sections (Career progress, Mustering): top-right quadrant,
    # ABOVE where the left-page tables begin. We cap their bottom just before the
    # next right-column heading (or the Skills header, which floats up to ~22pt
    # above the Skills heading line) so the Skills header can't leak in.
    prog_y = heading_y("career progress", x_lo=300)
    must_y = heading_y("mustering out", x_lo=300)
    skills_top = (skills_y - 24) if skills_y is not None else 330

    if prog_y is not None:
        end = (must_y - 2) if (must_y is not None and must_y > prog_y) else skills_top
        out += _trav_progress_section(words, page_no, prog_y + 8, end)
    if must_y is not None:
        out += _trav_mustering_section(words, page_no, must_y + 8, skills_top)

    # --- facing page: Mishaps (1D) + Events (2D), each a roll | description table.
    if mish_y is not None:
        end = ev_y - 2 if ev_y is not None and ev_y > mish_y else page_bottom
        out += _trav_roll_text_section(
            words, page_no, "Mishaps", ["1D", "Mishap"], mish_y, end, page_bottom
        )
    if ev_y is not None:
        out += _trav_roll_text_section(
            words, page_no, "Events", ["2D", "Event"], ev_y, page_bottom, page_bottom
        )
    return out


def _traveller_careers(page, page_no, existing) -> list[dict]:
    """Traveller career *anchor*: Mongoose careers are heading-anchored (a big
    career name over a 2-page spread of sub-tables), and the name often doesn't
    survive into the Markdown. We detect the name from page geometry and emit a
    one-per-career anchor card ``[[CAREER, name], [PAGE, n]]``; careers.py then
    assembles each career from the skill/rank tables on its page range."""
    text = page.get_text()
    # A real career spread carries several of these (Qualification, Survival,
    # Skills, Ranks, Mustering, …); sourcebook NPC/robot pages mention only a
    # couple incidentally, so require >=3 to skip them.
    if sum(m in text for m in _TRAV_CAREER_MARKERS) < 3:
        return []
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
        # Contents-derived single-domain chapters route header-less tables.
        item_chapters=frozenset({"EQUIPMENT"}),
        transport_chapters=frozenset({"VEHICLES", "COMMON SPACECRAFT"}),
        # Career-spread swap: detect a career page, then reconstruct its sub-tables.
        career_detect=is_traveller_career_page,
        career_sections=traveller_career_sections,
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
