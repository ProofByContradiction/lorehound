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

from .sources import CareerGeometry

# A PyMuPDF "word" is the tuple (x0, y0, x1, y1, text, block, line, word); name
# the fields we index so the geometry reads clearly instead of as bare numbers.
WORD_X0, WORD_Y0, WORD_X1, WORD_Y1, WORD_TEXT = 0, 1, 2, 3, 4


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


def _recover_trailing_rows(
    words: list, xe: list[float], ye: list[float], page_bottom: float, max_recover: int = 24
) -> list[float]:
    """Extend ``ye`` downward to recover data rows dropped below the last ruling line.

    ``find_tables(strategy="lines")`` omits a row band that has no bottom ruling
    line — common with alternating row shading, where only every other row is ruled.
    The word-bucketing in :func:`extract_tables` still recovers un-ruled rows that
    fall *between* detected bands (they sit inside the grid's y-range), but a row
    flush *below* the last band is past ``ye[-1]`` and lost — e.g. the D12 bottom row
    of the T2K "Chance of Success" tables.

    We walk downward one row-pitch at a time, taking the band of words flush below
    the grid and accepting it only while it forms a clean, column-aligned row: every
    word fits inside a single column (no word straddles a column boundary — prose
    does), the label column and at least two columns overall are filled, and the band
    sits within ~one pitch below (not past a blank-row gap). So a dropped row is
    recovered — including its wrapped continuation lines, which a pitch-tall band
    keeps with the row — but a paragraph or the next table below (separated by a gap,
    or not column-aligned) is not. The reach is sized to the detected row pitch, which
    runs tighter than the true row spacing, so each band lands on a single row rather
    than merging neighbours. Returns ``ye`` unchanged when nothing flush and aligned
    remains to recover.

    The alignment guards are what stop the walk (it halts the moment a band fails to
    be a clean grid row, e.g. a footnote line); ``max_recover`` is only a runaway
    backstop, so it is set well above any real table's dropped-row count.
    """
    nc = len(xe) - 1
    if nc < 2 or len(ye) < 3:
        return ye
    pitch = (ye[-1] - ye[0]) / (len(ye) - 1)
    if pitch <= 1:
        return ye
    reach = pitch + max(3.0, pitch * 0.6)

    def fits_one_column(w) -> bool:
        ci0, ci1 = _interval(w[WORD_X0], xe), _interval(w[WORD_X1], xe)
        return ci0 == ci1 and 0 <= ci0 < nc

    for _ in range(max_recover):
        top = ye[-1]
        band = [
            w for w in words
            if top + 1 <= (w[WORD_Y0] + w[WORD_Y1]) / 2 <= top + reach
            and xe[0] - 2 <= (w[WORD_X0] + w[WORD_X1]) / 2 <= xe[-1] + 2
            and w[WORD_TEXT].strip()
        ]
        if not band or not all(fits_one_column(w) for w in band):
            break
        cols = {_interval((w[WORD_X0] + w[WORD_X1]) / 2, xe) for w in band}
        if 0 not in cols or len(cols) < 2:
            break  # a real data row fills its label column and at least one more
        new_bottom = max(w[WORD_Y1] for w in band) + 1
        if new_bottom <= top or new_bottom > page_bottom:
            break
        ye = ye + [new_bottom]
    return ye


def _page_body_size(page) -> float:
    """The page's dominant body-text size (the size carrying the most characters),
    memoized. Used to tell heading lines from body/flavor lines."""
    cached = getattr(page, "_lh_bodysize", None)
    if cached is not None:
        return cached
    weight: dict[float, int] = defaultdict(int)
    for b in page.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            text = "".join(s["text"] for s in spans).strip()
            if text:
                weight[round(max(s["size"] for s in spans), 1)] += len(text)
    body = max(weight, key=weight.get) if weight else 8.0
    try:
        page._lh_bodysize = body
    except Exception:  # noqa: BLE001
        pass
    return body


def _title(page, x0: float, y0: float, x1: float, y1: float) -> str:
    """The table's title. Normally the heading line just above it — but weapon/gear
    stat cards put the item NAME in a heading font *above* a flavor paragraph, so the
    nearest line is the flavor, not the name. So: if the line immediately above is
    itself a heading (larger than the page body text) use it; otherwise look up to
    ~120pt for the nearest *entry heading* — a short, larger-than-body line that
    horizontally overlaps this table's column. The column check keeps a 2-column
    page's left/right cards from taking each other's name; the top-margin cutoff drops
    the page running-title.

    A font-based discriminator (to catch names rendered at the body *size* but in a
    distinct heading font, e.g. some Soviet weapons on T2K p103) is deferred — it
    regressed pages where size alone already worked (#66 option (b))."""
    body = _page_body_size(page)
    near = None   # (size, text, bottom_y) — nearest line in the 34pt window (orig logic)
    entry = None  # (bottom_y, text) — nearest entry heading within ~120pt
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            lb = line["bbox"]
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            size = max((s["size"] for s in line["spans"]), default=0.0)
            cx = (lb[0] + lb[2]) / 2
            if y0 - 34 <= lb[3] <= y0 + 1 and x0 - 2 <= cx <= x1 + 2:
                if near is None or size > near[0] + 0.5 or (
                    abs(size - near[0]) <= 0.5 and lb[3] > near[2]
                ):
                    near = (size, text, lb[3])
            if (
                lb[3] <= y0 + 1 and y0 - lb[3] < 120 and lb[0] < x1 + 2 and lb[2] > x0 - 2
                and size > body + 0.5 and len(text) <= 40 and lb[1] > 64
            ) and (entry is None or lb[3] > entry[0]):
                entry = (lb[3], text)
    if near is not None and near[0] > body + 0.5:
        return near[1]                      # a real heading sits right above — trust it
    if entry is not None:
        return entry[1]                     # the item name above the flavor paragraph
    return near[1] if near else ""


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
    # T2K archetype quick-build card (emitted by the archetype reconstructor).
    if "KEY ATTRIBUTE" in hdr:
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
    words = [w for w in page.get_text("words") if w[WORD_TEXT].strip()]
    if not words:
        return []

    def label_of(w) -> str | None:
        t = w[WORD_TEXT].strip().lower().strip(":")
        if t == _CAREER_HDR:
            return "career"
        return t if t in _CAREER_FIELDS else None

    tagged = [(w, label_of(w)) for w in words if label_of(w)]
    if not tagged:
        return []
    label_x = min(w[WORD_X0] for w, _ in tagged)
    labels = [(w, l) for w, l in tagged if abs(w[WORD_X0] - label_x) <= 12]
    header_ys = sorted(w[WORD_Y0] for w, l in labels if l == "career")
    if not header_ys:
        return []

    grids: list[dict] = []
    for hi, career_y in enumerate(header_ys):
        seg_end = header_ys[hi + 1] if hi + 1 < len(header_ys) else page.rect.height
        # Field-label anchors within this header's segment.
        anchors: list[float] = []
        for y in sorted(round(w[WORD_Y0]) for w, _ in labels if career_y - 1 <= w[WORD_Y0] < seg_end):
            if not anchors or y - anchors[-1] > 6:
                anchors.append(y)
        if len({l for w, l in labels if career_y - 1 <= w[WORD_Y0] < seg_end} - {"career"}) < 2:
            continue  # not enough field rows to be a career table
        # Specialty roll rows (1..9 in the label column, below the SPECIALTY label).
        spec_ys = [w[WORD_Y0] for w, l in labels if l.startswith("special") and career_y <= w[WORD_Y0] < seg_end]
        if spec_ys:
            spec_y = min(spec_ys)
            for w in words:
                t = w[WORD_TEXT].strip()
                if (
                    t.isdigit() and 1 <= int(t) <= 9
                    and abs(w[WORD_X0] - label_x) <= 12
                    and spec_y + 2 < w[WORD_Y0] < seg_end
                ):
                    y = round(w[WORD_Y0])
                    if all(abs(y - a) > 6 for a in anchors):
                        anchors.append(y)
            anchors.sort()

        nxt = min((a for a in anchors if a > career_y + 3), default=career_y + 20)
        hdr_words = [w for w in words if career_y - 2 <= w[WORD_Y0] < nxt - 2 and w[WORD_X0] >= label_x - 4]
        cols = _col_starts(sorted(w[WORD_X0] for w in hdr_words))
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
                if top - 3 <= w[WORD_Y0] < bottom - 3 and w[WORD_X0] >= label_x - 6:
                    buckets[col_of(w[WORD_X0])].append(w)
            row = [
                " ".join(x[WORD_TEXT] for x in sorted(b, key=lambda w: (round(w[WORD_Y0] / 3), w[WORD_X0])))
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


def _shaded_table_regions(page) -> list:
    """Clip rects for table regions marked by alternating *shaded row-bands* (filled
    rectangles) rather than ruling lines — the Paizo/Pathfinder style that
    ``find_tables(strategy="lines")`` is blind to. Bands (wide, short fills) are
    clustered by vertical proximity + horizontal overlap; each group of >=2 becomes a
    region padded up for a header row and down for an un-shaded final row."""
    import fitz

    try:
        draws = page.get_drawings()
    except Exception:  # noqa: BLE001
        return []
    bands = [
        d["rect"] for d in draws
        if d.get("fill") and d.get("rect") is not None
        and d["rect"].width > 90 and 3 < d["rect"].height < 28
    ]
    if len(bands) < 2:
        return []
    bands.sort(key=lambda r: r.y0)
    groups: list[list] = [[bands[0]]]
    for b in bands[1:]:
        last = groups[-1][-1]
        if b.y0 - last.y1 <= 30 and not (b.x1 < last.x0 - 5 or b.x0 > last.x1 + 5):
            groups[-1].append(b)
        else:
            groups.append([b])
    regions = []
    for g in groups:
        if len(g) < 2:  # need >=2 alternating bands to read as a table
            continue
        x0, x1 = min(r.x0 for r in g), max(r.x1 for r in g)
        y0, y1 = min(r.y0 for r in g), max(r.y1 for r in g)
        pitch = (y1 - y0) / (len(g) - 1)
        regions.append(fitz.Rect(x0 - 3, y0 - pitch * 1.6, x1 + 3, y1 + pitch * 1.3))
    return regions


def _clean_grid(raw: list) -> list[list[str]]:
    """Strip cells, pad ragged rows, drop empty columns then empty rows."""
    rows = [[(c or "").strip() for c in r] for r in (raw or []) if r]
    if not rows:
        return []
    nc = max(len(r) for r in rows)
    rows = [r + [""] * (nc - len(r)) for r in rows]
    keep = [i for i in range(nc) if any(r[i].strip() for r in rows)]
    rows = [[r[i] for i in keep] for r in rows]
    return [r for r in rows if any(c.strip() for c in r)]


def _frag_fraction(rows: list[list[str]]) -> float:
    """Fraction of non-empty cells that start with a lowercase letter — a proxy for
    column over-segmentation. Clean stat-table cells start uppercase/digit; a column
    split mid-word (``Bu``/``lk``, ``Wooden``/``shield``) leaves lowercase
    continuation fragments. Lower is cleaner; used to pick between candidates."""
    cells = [c for r in rows for c in r if c.strip()]
    if not cells:
        return 1.0
    return sum(1 for c in cells if c[:1].islower()) / len(cells)


def _geometric_region_rows(words: list, reg) -> list[list[str]]:
    """Reconstruct a shaded region's grid from word geometry: cluster word
    x-positions into column anchors and bucket words into (row-band, column). Gives
    cleaner columns than the text strategy for stat tables (no ``Bu``/``lk`` splits),
    but can split a multi-word name cell — so the caller keeps whichever candidate is
    less fragmented."""
    sel = [
        w for w in words
        if reg.x0 <= w[WORD_X0] < reg.x1 and reg.y0 <= w[WORD_Y0] < reg.y1 and w[WORD_TEXT].strip()
    ]
    if len(sel) < 6:
        return []
    starts = _trav_col_anchors(sel, gap=18.0, numeric_only=False)
    if len(starts) < 3:
        return []
    return _clean_grid(_trav_band_rows(sel, reg.x0, reg.x1, reg.y0, reg.y1, starts))


def _recover_shaded_tables(page, page_no: int, found: list) -> list[dict]:
    """Recover shaded (un-ruled) tables the lines pass misses entirely. For each
    shaded region not already covered by a lines-detected table, build two candidate
    reconstructions — a text-strategy pass *clipped to the region* (so it captures the
    table, not surrounding prose) and a geometric word-bucketing pass — and keep the
    less column-fragmented one (Stage B). Leaves ruled books untouched: their tables
    are found by lines, so the regions overlap and are skipped.
    """
    regions = _shaded_table_regions(page)
    if not regions:
        return []
    covered = [t.bbox for t in found]
    words = page.get_text("words")

    def overlaps(reg) -> bool:
        for x0, y0, x1, y1 in covered:
            if min(reg.y1, y1) - max(reg.y0, y0) > 4 and min(reg.x1, x1) - max(reg.x0, x0) > 4:
                return True
        return False

    out: list[dict] = []
    for reg in regions:
        if overlaps(reg):
            continue
        text_rows = []
        try:
            tabs = page.find_tables(clip=reg, strategy="text").tables
            if tabs:
                text_rows = _clean_grid(tabs[0].extract())
        except Exception:  # noqa: BLE001
            pass
        geo_rows = _geometric_region_rows(words, reg)
        # Require >=3 columns: a clipped pass over a shaded *prose* box (e.g. a ship's
        # description sidebar) yields 2 columns of fragments; genuine shaded stat
        # tables are wider. (Real 2-col tables are ruled → already found by lines.)
        cands = [r for r in (geo_rows, text_rows) if len(r) >= 2 and len(r[0]) >= 3]
        if not cands:
            continue
        rows = min(cands, key=_frag_fraction)  # the cleaner (less-fragmented) candidate
        out.append({
            "page": page_no,
            "title": _title(page, reg.x0, reg.y0, reg.x1, reg.y1),
            "rows": rows,
        })
    return out


# A roll-index header cell: '1D', '2D', 'D6', '2D6', 'D100' — requires a digit so a
# bare 'D' data value (e.g. a T2K reliability rating) isn't mistaken for a header.
_DIE_HEADER = re.compile(r"\d+[dD]\d*|[dD]\d+")


def _is_roll_row(r: list[str]) -> bool:
    """A roll-indexed data row: first cell is a roll result like '1', '12' or '2–3'."""
    return bool(r) and bool(re.fullmatch(r"\d{1,2}([–-]\d{1,2})?", (r[0] or "").strip()))


def _split_stacked_tables(rows: list[list[str]]) -> list[list[list[str]]]:
    """Split a *roll table* that is really several stacked together. find_tables merges
    two adjacent roll tables (e.g. a Traveller career's Skills Table A + specialist
    Table B, or its Mishaps + Events) when no per-system reconstructor fires. We only
    touch tables whose header's first cell is a die label ('1D'/'2D'/'D6'), and split
    at each mid-table die-label header, trimming trailing non-roll rows (a stray
    'EVENTS TABLE' banner). This deliberately ignores non-roll tables (weapon/career
    cards, equipment) so it can't shred them. ``[rows]`` when nothing splits."""
    if len(rows) < 4 or not rows[0] or not _DIE_HEADER.fullmatch((rows[0][0] or "").strip()):
        return [rows]

    def is_header(r: list[str], i: int) -> bool:
        if i == 0:
            return False
        rest = [c.strip() for c in r[1:] if c.strip()]
        return (
            bool(_DIE_HEADER.fullmatch((r[0] or "").strip()))
            and bool(rest) and all(len(c) <= 25 for c in rest)
            and any(any(ch.isalpha() for ch in c) for c in rest)
        )

    bounds = [0] + [i for i, r in enumerate(rows) if is_header(r, i)] + [len(rows)]
    segs = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        seg = rows[a:b]
        while len(seg) > 1 and not _is_roll_row(seg[-1]):  # trim trailing banners/junk
            seg = seg[:-1]
        if len(seg) >= 2:
            segs.append(seg)
    return segs if len(segs) > 1 else [rows]


def _is_catalog_header(r: list[str]) -> bool:
    """A column-label row of a catalog (``… Price Damage Bulk Hands Group …``): three
    or more short label cells past the name column, each a real word — digit-free and
    containing a letter. The letter rule rejects placeholder-laden stat-block rows
    ('Fuel Scoops —  —'); the no-digit rule rejects data rows ('1 gp', '1d8 S')."""
    cells = [c.strip() for c in r[1:] if c and c.strip()]
    return len(cells) >= 3 and all(
        len(c) <= 25 and any(ch.isalpha() for ch in c) and not any(ch.isdigit() for ch in c)
        for c in cells
    )


def _catalog_header_sig(r: list[str]) -> tuple:
    return tuple(c.strip().lower() for c in r[1:] if c and c.strip())


def _split_stacked_catalogs(rows: list[list[str]]) -> list[list[list[str]]]:
    """Split a *catalog* table that find_tables merged from several stacked sub-tables
    (the Pathfinder weapons page: Simple / Martial / Advanced glued together, each under
    the same ``… Price Damage Bulk Hands Group …`` header, with section-label rows
    ('Unarmed') and leaked captions ('TABLE 6–7: MELEE…') between them).

    Split ONLY at a header that REPEATS — the identical column-label signature appearing
    ≥2×. That repetition is what tells a genuine stacked catalog from a career card or a
    ship stat block (whose rows look header-ish individually but never repeat a
    signature), so those aren't shredded. Trailing caption/label rows are trimmed.
    ``[rows]`` when nothing splits."""
    if len(rows) < 6:
        return [rows]
    from collections import Counter

    counts = Counter(_catalog_header_sig(r) for r in rows if _is_catalog_header(r))
    repeated = {sig for sig, n in counts.items() if n >= 2}
    if not repeated:
        return [rows]
    hdr_idx = [
        i for i, r in enumerate(rows)
        if _is_catalog_header(r) and _catalog_header_sig(r) in repeated
    ]
    if len(hdr_idx) < 2:
        return [rows]

    def is_data(r: list[str]) -> bool:
        c0 = (r[0] or "").strip()
        others = [c for c in r[1:] if c and c.strip()]
        return bool(c0) and any(ch.isalpha() for ch in c0) and len(others) >= 2

    bounds = hdr_idx + [len(rows)]
    segs = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        seg = rows[a:b]
        while len(seg) > 1 and not is_data(seg[-1]):  # trim trailing caption/label rows
            seg = seg[:-1]
        if len(seg) >= 2:                              # header + ≥1 item
            segs.append(seg)
    return segs if len(segs) > 1 else [rows]


def _split_table_dict(t: dict) -> list[dict]:
    """Fan a table dict out into one dict per recovered sub-table — first the roll-table
    split (Mishaps+Events), then the stacked-catalog split (the Pathfinder weapons
    blob). Roll halves derive their title from their header's label column; catalog
    halves from their header's NAME column ('Simple Weapons', 'Martial Weapons'), which
    also lets the equipment-routing reclassify each half from 'tables' to 'items'."""
    rows = t.get("rows") or []
    segs = _split_stacked_tables(rows)
    if len(segs) > 1:
        out = [{**t, "rows": segs[0]}]
        for s in segs[1:]:
            label = s[0][1].strip() if len(s[0]) >= 2 else ""
            out.append({**t, "title": label.title() if label else t.get("title", ""), "rows": s})
        return out
    segs = _split_stacked_catalogs(rows)
    if len(segs) > 1:
        out = []
        for s in segs:
            label = s[0][0].strip() if s and s[0] else ""
            out.append({**t, "title": label or t.get("title", ""), "rows": s})
        return out
    return [t]


def _dedupe_words(words: list) -> list:
    """Drop words rendered twice at the same spot. Some PDFs double-strike text for
    faux-bold, so get_text returns each word twice — e.g. the Pathfinder ability-
    modifiers table reads '1 –5 1 –5'. Keep one per (x, y, text); a no-op for normal
    pages (no exact-position duplicates)."""
    seen: set = set()
    out = []
    for w in words:
        key = (round(w[WORD_X0]), round(w[WORD_Y0]), w[WORD_TEXT])
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def _unroll_repeated_columns(rows: list[list[str]]) -> list[list[str]]:
    """If the header row is M>=2 identical copies of a K-column group — a side-by-side
    layout used to save vertical space (e.g. the Pathfinder ability-modifiers table's
    two ``Score | Modifier`` halves) — stack the M groups into one K-column table.
    No-op when the header isn't a clean repeat."""
    if len(rows) < 2:
        return rows
    hdr = [c.strip() for c in rows[0]]
    n = len(hdr)
    for k in range(1, n // 2 + 1):
        if n % k or n // k < 2:
            continue
        groups = [hdr[i:i + k] for i in range(0, n, k)]
        if any(groups[0]) and all(g == groups[0] for g in groups):
            m = n // k
            out = [groups[0]]
            for r in rows[1:]:
                r = list(r) + [""] * (n - len(r))
                for j in range(m):
                    seg = r[j * k:(j + 1) * k]
                    if any(c.strip() for c in seg):
                        out.append(seg)
            return out
    return rows


def _maybe_unroll_sidebyside(page, words, xe, ye, rows):
    """A narrow lines-detected table may really be a side-by-side repeated layout that
    find_tables merged into a few wide columns. Rebuild it from word geometry and, if
    the columns form a repeated group, unroll them. Returns the unrolled rows when it
    applies (and is a strict improvement), else the original rows."""
    if not rows or len(rows[0]) > 4:
        return rows
    # Extend the top above ye[0] to capture the header row (it sits just above the
    # first ruled band), so the repeated column group is visible to the unroller.
    pitch = (ye[-1] - ye[0]) / (len(ye) - 1) if len(ye) > 1 else 12.0
    y_top = ye[0] - max(12.0, pitch * 1.5)
    reg = [
        w for w in words
        if xe[0] - 2 <= w[WORD_X0] < xe[-1] + 2 and y_top <= w[WORD_Y0] < ye[-1] + 2
    ]
    starts = _trav_col_anchors(reg, gap=20.0, numeric_only=False)
    if len(starts) < 4:  # need >=2 groups of >=2 columns
        return rows
    geo = _trav_drop_empty_cols([
        r for r in _trav_band_rows(reg, xe[0], xe[-1], y_top, ye[-1], starts)
        if any(c.strip() for c in r)
    ])
    unrolled = _unroll_repeated_columns(geo)
    if unrolled is not geo and len(unrolled[0]) < len(geo[0]) and len(unrolled) > len(rows):
        return unrolled
    return rows


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

    # De-dup double-struck words (faux-bold renders each word twice) before bucketing.
    words = _dedupe_words(page.get_text("words"))  # (x0, y0, x1, y1, text, block, line, word)
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
            # Recover rows dropped below the last ruling line (un-ruled bottom row,
            # e.g. the T2K "Chance of Success" D12 row) before bucketing words.
            ye = _recover_trailing_rows(words, xe, ye, page.rect.height)
            nc, nr = len(xe) - 1, len(ye) - 1
            grid = [["" for _ in range(nc)] for _ in range(nr)]
            for w in words:
                cx, cy = (w[WORD_X0] + w[WORD_X1]) / 2, (w[WORD_Y0] + w[WORD_Y1]) / 2
                if not (xe[0] - 2 <= cx <= xe[-1] + 2 and ye[0] - 2 <= cy <= ye[-1] + 2):
                    continue
                ci, ri = _interval(cx, xe), _interval(cy, ye)
                if 0 <= ci < nc and 0 <= ri < nr:
                    grid[ri][ci] = (grid[ri][ci] + " " + w[WORD_TEXT]).strip()
            # Drop empty columns, then empty rows.
            keep = [i for i in range(nc) if any(grid[r][i].strip() for r in range(nr))]
            rows = [[row[i] for i in keep] for row in grid]
            rows = [r for r in rows if any(c.strip() for c in r)]
            # Unroll side-by-side repeated layouts (e.g. the two Score|Modifier halves
            # of the Pathfinder ability-modifiers table) into one column group.
            rows = _maybe_unroll_sidebyside(page, words, xe, ye, rows)
            if len(rows) >= 2 and len(rows[0]) >= 2:
                out.append(
                    {
                        "page": page_no,
                        "title": _title(page, xe[0], ye[0], xe[-1], ye[-1]),
                        "rows": rows,
                    }
                )
    # Ruling-independent fallback: recover shaded (un-ruled) tables — Paizo/Pathfinder
    # style — that the lines pass is blind to, without disturbing ruled books.
    out.extend(_recover_shaded_tables(page, page_no, found))
    # Source-specific geometric reconstructions (career grids, gear cards, ship
    # blocks) for layouts the generic pass can't recover; baseline-only otherwise.
    if profile is not None:
        out.extend(profile.reconstruct(page, page_no, out))
    # Split any two-tables-merged-into-one (a mid-table secondary header), e.g. a
    # career's Skills A/B or Mishaps/Events on books with no career reconstructor.
    return [sub for t in out for sub in _split_table_dict(t)]


def _t2k_careers(page, page_no, existing) -> list[dict]:
    """T2K career reconstructor: rebuild column career-cards geometrically, but
    only where find_tables didn't already capture a clean one (fallback)."""
    if _has_clean_career_card(existing):
        return []
    return _career_grids(page, page_no)


# T2K archetype "quick build" stat box — styled text the markdown pass drops.
_ARCH_ATTR = re.compile(r"KEY ATTRIBUTE:\s*([A-Z]{3})")
_ARCH_SKILLS = re.compile(r"KEY SKILLS:\s*(.+?)\s*(?:✓|COOLNESS|KEY |\t)", re.S)
_ARCH_CUF = re.compile(r"COOLNESS UNDER FIRE:\s*([A-D])")
_ARCH_SPECS = re.compile(r"SPECIALTIES\b.*?\n(.*?)(?:YOUR MORAL|MORAL CODE|YOUR BIG|GEAR)", re.S)
_ARCH_BULLET = re.compile(r"[7✓]\s*([A-Z][A-Za-z /()'’-]+)")


def _t2k_archetypes(page, page_no, existing) -> list[dict]:
    """T2K archetype reconstructor: each archetype page carries a styled stat box
    (KEY ATTRIBUTE / KEY SKILLS / recommended SPECIALTIES) that the markdown pass
    drops, so the chargen archetype flow has nothing to read. Recover it from the
    page's raw text and emit one structured card per archetype page; ``[]`` elsewhere.
    The archetype *name* is a graphic, so it isn't here — the prose parser pairs this
    card with the markdown ``#### The …`` heading on the same page."""
    raw = page.get_text()
    attr = _ARCH_ATTR.search(raw)
    skills = _ARCH_SKILLS.search(raw)
    if not attr or not skills:
        return []
    skill_list = [s.strip() for s in re.sub(r"\s+", " ", skills.group(1)).split(",") if s.strip()]
    if len(skill_list) < 2:
        return []
    rows = [["KEY ATTRIBUTE", attr.group(1)], ["KEY SKILLS", ", ".join(skill_list)]]
    cuf = _ARCH_CUF.search(raw)
    if cuf:
        rows.append(["COOLNESS UNDER FIRE", cuf.group(1)])
    sp = _ARCH_SPECS.search(raw)
    if sp:
        specs = [s.strip() for s in _ARCH_BULLET.findall(sp.group(1))][:6]
        if specs:
            rows.append(["SPECIALTIES", ", ".join(specs)])
    return [{"page": page_no, "title": "ARCHETYPE", "rows": rows}]


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

# The Mongoose 2e career-spread layout (CareerGeometry's defaults). Shared by the
# section reconstructors below as their default ``geom`` and registered on the
# Traveller profile; a differently-laid-out book supplies its own instead.
_MONGOOSE_CAREER_GEOMETRY = CareerGeometry()


def _trav_y_bands(words, tol: float = 4.0) -> list[float]:
    """Sorted row-band tops: y-coordinates collapsed so words on the same text
    line share a band (a new band starts when y jumps more than ``tol``)."""
    bands: list[float] = []
    for y in sorted(round(w[WORD_Y0]) for w in words):
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
        row = sorted((w for w in words if top - 4 < w[WORD_Y0] < bottom - 4), key=lambda w: w[WORD_X0])
        if not row:
            continue
        if numeric_only:
            if not _TRAV_NUM.fullmatch(row[0][WORD_TEXT].strip()):
                continue
            # The roll index is its own (short, far-left) column even when the next
            # column sits closer than ``gap`` (Navy's PD column starts ~20pt right
            # of the roll). Seed col0 at the roll's x; cluster the rest beyond it.
            roll_xs.append(row[0][WORD_X0])
            data = [w for w in row[1:] if w[WORD_X0] > row[0][WORD_X0] + 14]
        else:
            data = row
        prev = None
        rs: list[float] = []
        for w in data:
            if prev is None or w[WORD_X0] - prev > gap:
                rs.append(w[WORD_X0])
            prev = w[WORD_X0]
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
    sel = [w for w in words if x_lo <= w[WORD_X0] < x_hi and y_lo <= w[WORD_Y0] < y_hi and w[WORD_TEXT].strip()]
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
            if top - tol < w[WORD_Y0] < bottom - tol:
                cells[col_of(w[WORD_X0])].append(w)
        rows.append([" ".join(x[WORD_TEXT] for x in sorted(b, key=lambda w: w[WORD_X0])) for b in cells])
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


def _page_dict(page) -> dict:
    """``page.get_text("dict")`` memoized on the page object. Career detection runs
    the heading scan on *every* page (3× per page: career-name + Mishaps + Events
    checks) and the reconstructor reruns it on career pages — caching collapses all
    of that to one parse per page. The same page object is reused across
    career_detect/career_sections, so the cache lands. Best-effort: if the object
    rejects the attribute we simply re-parse (correct, just not cached)."""
    cached = getattr(page, "_lh_dict", None)
    if cached is None:
        cached = page.get_text("dict")
        try:
            page._lh_dict = cached
        except Exception:  # noqa: BLE001 - some page objects reject attrs; recompute
            pass
    return cached


def _page_words(page) -> list:
    """``page.get_text("words")`` memoized on the page object (see :func:`_page_dict`)."""
    cached = getattr(page, "_lh_words", None)
    if cached is None:
        cached = page.get_text("words")
        try:
            page._lh_words = cached
        except Exception:  # noqa: BLE001
            pass
    return cached


def _trav_section_headings(page) -> list[tuple[str, float, float, float]]:
    """(text, size, x0, y0) for every line on the page (heading detection by the
    caller). Multi-span lines are joined; size is the line's max span size."""
    out: list[tuple[str, float, float, float]] = []
    for b in _page_dict(page).get("blocks", []):
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
    for b in _page_dict(page).get("blocks", []):
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
        cx, cy = (w[WORD_X0] + w[WORD_X1]) / 2, (w[WORD_Y0] + w[WORD_Y1]) / 2
        return any(x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1
                   for x0, y0, x1, y1 in rects)

    return [w for w in _page_words(page) if w[WORD_TEXT].strip() and not in_heading(w)]


def _trav_skills_sections(words, page_no, y_lo, y_hi, geom=_MONGOOSE_CAREER_GEOMETRY) -> list[dict]:
    """Reconstruct the "Skills and training" band into Table A (the universal
    Roll | Personal Development | Service Skills | Advanced Education grid) and, if
    present, Table B (the per-assignment specialist-skills grid). The two stack in
    one band; a second ``1D``-led uppercase row marks B's header, splitting them."""
    band = [w for w in words if y_lo <= w[WORD_Y0] < y_hi]
    starts = _trav_col_anchors(band, gap=30.0, numeric_only=True)
    rows = _trav_band_rows(words, geom.left_band[0], geom.left_band[1], y_lo, y_hi, starts)
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


def _trav_ranks_section(words, page_no, y_lo, y_hi, geom=_MONGOOSE_CAREER_GEOMETRY) -> list[dict]:
    """Reconstruct the "Ranks and bonuses" band: RANK | <category> | SKILL OR BONUS.
    A career can stack two rank tracks (e.g. Agent's enlisted vs intelligence), each
    with its own ``RANK``-led header; we keep them as one table (the inline second
    header reads fine as a sub-divider)."""
    band = [w for w in words if y_lo <= w[WORD_Y0] < y_hi]
    starts = _trav_col_anchors(band, gap=30.0, numeric_only=True)
    rows = _trav_band_rows(words, geom.left_band[0], geom.left_band[1], y_lo, y_hi, starts)
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


def _trav_progress_section(words, page_no, y_lo, y_hi, geom=_MONGOOSE_CAREER_GEOMETRY) -> list[dict]:
    """Reconstruct the "Career progress" survival/advancement table: a blank-headed
    assignment column then SURVIVAL and ADVANCEMENT (their checks per assignment)."""
    x_lo, x_hi = geom.right_band
    band = [w for w in words if x_lo <= w[WORD_X0] < x_hi and y_lo <= w[WORD_Y0] < y_hi]
    starts = _trav_col_anchors(band, gap=30.0, numeric_only=False)
    rows = _trav_drop_empty_cols(_trav_band_rows(words, x_lo, x_hi, y_lo, y_hi, starts))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2 or len(rows[0]) < 2:
        return []
    if not rows[0][0].strip():  # name the blank assignment-column header
        rows[0][0] = "Assignment"
    return [{"page": page_no, "title": "Career progress", "rows": rows}]


def _trav_mustering_section(words, page_no, y_lo, y_hi, geom=_MONGOOSE_CAREER_GEOMETRY) -> list[dict]:
    """Reconstruct "Mustering out benefits": 1D | CASH | BENEFITS."""
    x_lo, x_hi = geom.right_band
    band = [w for w in words if x_lo <= w[WORD_X0] < x_hi and y_lo <= w[WORD_Y0] < y_hi]
    starts = _trav_col_anchors(band, gap=30.0, numeric_only=True)
    rows = _trav_drop_empty_cols(_trav_band_rows(words, x_lo, x_hi, y_lo, y_hi, starts))
    rows = [r for r in rows if any(c.strip() for c in r)]
    rows = _trav_merge_headerless_cols(rows)  # fold a wrapped-benefit spill column
    if len(rows) < 3 or len(rows[0]) < 2:
        return []
    return [{"page": page_no, "title": "Mustering out benefits", "rows": rows}]


# Tokens that aren't part of a Mishaps/Events description: the small-caps column
# labels printed above each table, and a stray "table"/page reference.
_TRAV_ROLL_NOISE = frozenset({"MISHAP", "EVENT", "MISHAPS", "EVENTS"})


def _trav_roll_text_section(words, page_no, title, header, y_lo, y_hi, page_h=792.0,
                            geom=_MONGOOSE_CAREER_GEOMETRY) -> list[dict]:
    """Reconstruct a roll | description table (Mishaps / Events), where each
    description wraps over several text lines. Rows are keyed by the roll-index word
    in the left column; the description gathers every body word down to the next
    roll index. The roll column's x varies a little per career (~79–86), so we
    locate it from the leftmost numeric words rather than hardcoding it."""
    foot = page_h - 24  # drop the page-number folio in the bottom margin
    x_lo, x_hi = geom.roll_band

    def keep(w) -> bool:
        return (
            x_lo <= w[WORD_X0] < x_hi and y_lo <= w[WORD_Y0] < min(y_hi, foot)
            and w[WORD_TEXT].strip() and w[WORD_TEXT].strip().upper() not in _TRAV_ROLL_NOISE
        )

    sel = [w for w in words if keep(w)]
    nums = [w for w in sel if w[WORD_X0] < geom.roll_index_max and _TRAV_NUM.fullmatch(w[WORD_TEXT].strip())]
    if not nums:
        return []
    roll_x = min(w[WORD_X0] for w in nums)         # the roll column's left edge
    rolls = [w for w in nums if w[WORD_X0] <= roll_x + 12]
    body_x = roll_x + 22                            # descriptions sit ~25pt right
    rolls.sort(key=lambda w: w[WORD_Y0])
    rows = [list(header)]
    for i, rw in enumerate(rolls):
        top = rw[WORD_Y0] - 2
        bottom = rolls[i + 1][WORD_Y0] - 2 if i + 1 < len(rolls) else min(y_hi, foot)
        body = [w for w in sel if w[WORD_X0] >= body_x and top <= w[WORD_Y0] < bottom]
        body.sort(key=lambda w: (round(w[WORD_Y0] / 3), w[WORD_X0]))
        text = " ".join(w[WORD_TEXT] for w in body).strip()
        if text:
            rows.append([rw[WORD_TEXT].strip(), text])
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


def traveller_career_sections(page, page_no: int, geom=None) -> list[dict]:
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
    geom = geom or _MONGOOSE_CAREER_GEOMETRY
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
    skills_y = heading_y("skills and training", x_hi=geom.column_split)
    ranks_y = heading_y("ranks and bonuses", x_hi=geom.ranks_heading_max)
    mish_y = heading_y("mishaps", x_hi=geom.column_split)
    ev_y = heading_y("events", x_hi=geom.column_split)
    left_heads = sorted(y for y in (skills_y, ranks_y, mish_y, ev_y) if y is not None)

    def left_end(start):
        return min((y for y in left_heads if y > start + 2), default=page_bottom) - 2

    if skills_y is not None:
        # The (uppercase) Table-A header sits a little ABOVE the vertical heading's
        # top, so start the band above it; end at the next left-page heading.
        out += _trav_skills_sections(words, page_no, skills_y - 22, left_end(skills_y), geom)
    if ranks_y is not None:
        out += _trav_ranks_section(words, page_no, ranks_y - 22, left_end(ranks_y), geom)

    # --- right-column sections (Career progress, Mustering): top-right quadrant,
    # ABOVE where the left-page tables begin. We cap their bottom just before the
    # next right-column heading (or the Skills header, which floats up to ~22pt
    # above the Skills heading line) so the Skills header can't leak in.
    prog_y = heading_y("career progress", x_lo=geom.column_split)
    must_y = heading_y("mustering out", x_lo=geom.column_split)
    skills_top = (skills_y - 24) if skills_y is not None else 330

    if prog_y is not None:
        end = (must_y - 2) if (must_y is not None and must_y > prog_y) else skills_top
        out += _trav_progress_section(words, page_no, prog_y + 8, end, geom)
    if must_y is not None:
        out += _trav_mustering_section(words, page_no, must_y + 8, skills_top, geom)

    # --- facing page: Mishaps (1D) + Events (2D), each a roll | description table.
    if mish_y is not None:
        end = ev_y - 2 if ev_y is not None and ev_y > mish_y else page_bottom
        out += _trav_roll_text_section(
            words, page_no, "Mishaps", ["1D", "Mishap"], mish_y, end, page_bottom, geom
        )
    if ev_y is not None:
        out += _trav_roll_text_section(
            words, page_no, "Events", ["2D", "Event"], ev_y, page_bottom, page_bottom, geom
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
    for b in _page_dict(page).get("blocks", []):
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
        reconstructors=[_t2k_careers, _t2k_archetypes],
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
        # Career-spread swap: detect a career page, then reconstruct its sub-tables
        # using the Mongoose 2e page-layout coordinates.
        career_detect=is_traveller_career_page,
        career_sections=traveller_career_sections,
        career_geometry=_MONGOOSE_CAREER_GEOMETRY,
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
