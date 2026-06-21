"""Glue between Google Drive and the search index.

Pulls documents, groups them by game (top-level Drive subfolder), parses their
(now Markdown) structure into heading-aware, categorized chunks, and answers
scoped searches. PyMuPDF extraction (in drive_client) yields Markdown whose
headings we use to (a) tag each chunk with a section breadcrumb and (b) classify
it as "rules" (character abilities/procedures) vs "stuff" (gear & vehicles).
"""

from __future__ import annotations

import re

from .drive_client import DriveClient
from .search_index import Chunk, SearchHit, SearchIndex
from .tables import parse_picture_rows

_PAGE_MARKER = re.compile(r"\[\[page (\d+)\]\]")
_MD_HEADER = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# Twilight 2000 page-top chapter line, e.g. "04 COMBAT & DAMAGE".
_T2K_CHAPTER = re.compile(r"^(\d{2})\s+([A-Z][A-Z0-9 &'/.\-]{2,})\s*$")
# PyMuPDF placeholders/markers to drop from body text.
_PICTURE = re.compile(r"==>.*omitted.*<==|----- (Start|End) of picture text -----")
# Free League PDF watermark footer leaks into the text (e.g. "0 75 Adam Delaura
# (Order #51743052)"); drop any line carrying the order-watermark.
_WATERMARK = re.compile(r"\(Order #\d+\)")

# An OCR'd image-table block, plus the structural markers we scan so each table
# can be labelled with the heading above it. PyMuPDF emits image tables between
# these markers with <br> between rows; the main prose pass drops them, so we
# recover them here (see _tables_for_doc + tables.py).
_PIC_BLOCK = re.compile(
    r"-----\s*Start of picture text\s*-----\**(?:<br>)?\s*(?P<body>.*?)\s*"
    r"\**-----\s*End of picture text\s*-----",
    re.S,
)
_SCAN_PAGE = re.compile(r"\[\[page (\d+)\]\]")
_SCAN_MD = re.compile(r"(?m)^(#{1,6})[ \t]+([^\n]+?)[ \t]*$")
_SCAN_T2K = re.compile(r"(?m)^(\d{2})[ \t]+([A-Z][A-Z0-9 &'/.\-]{2,})[ \t]*$")

# A "definition entry": a bold lead-in term with a colon, optionally bulleted —
# e.g. "- **SNIPER:** Gives a +1 modifier…" or "**Ballistic Vest**: …". RPG books
# use this shape for specialties, skills, talents, gear, conditions, and the like.
# We split each into its own chunk so a lookup returns just that entry instead of
# the whole list it lives in. (Leading "\d*" eats the stray bullet glyph PyMuPDF
# sometimes emits, e.g. the "7" in "- 7 **SNIPER:**".)
_DEF_ENTRY = re.compile(
    r"^[\s\-•▪◦·]*\d*\s*\*\*(?P<term>[A-Z0-9][^*\n]{0,38}?)(?::\*\*|\*\*:)\s*"
    r"(?P<body>\S.*)$"
)

_TARGET_WORDS = 110

# Heading/book keywords used to classify a chunk. "rules" = how to play
# (character stats, abilities, specialties, procedures). "items" and "vehicles"
# = the tools players use. Matched against book name + chapter/section headings
# only (not body), most-specific-first, so a rule that merely mentions a weapon
# stays a rule. Vehicles take precedence over items (e.g. "ship weapon").
_VEHICLE = re.compile(
    r"\b(vehicle|vehicles|ship|ships|starship|spacecraft|small craft|high guard|"
    r"adventure class|aircraft|watercraft|boat|tank|mecha|hull|thruster|"
    r"spinal mount|turret)\b",
    re.I,
)
_ITEM = re.compile(
    r"\b(gear|equipment|equip|weapon|weapons|armou?r|supply|catalog|catalogue|"
    r"item|items|kit|robot|robots|drone|drones|gadget|loadout|trade good|"
    r"cybernetic|augment|firearm|rifle|pistol|shotgun|grenade|ammunition|melee)\b",
    re.I,
)


def _category(book: str, chapter: str, section: str) -> str:
    """Classify as 'rules', 'items', or 'vehicles' from the most specific heading."""
    for text in (section, chapter, book):
        if not text:
            continue
        if _VEHICLE.search(text):
            return "vehicles"
        if _ITEM.search(text):
            return "items"
    return "rules"


# Strong stat-table signatures in the body (Markdown tables from PyMuPDF). Used
# to re-tag weapon/vehicle entries that hide under rules-chapter headings (e.g.
# T2K weapon write-ups in the Combat chapter). Real rules never carry these.
_WEAPON_TABLE = re.compile(r"\|\s*ROF\s*\||\|\s*WEAPON\s*\|", re.I)
_VEHICLE_TABLE = re.compile(
    r"\|\s*(?:TRAVEL SPEED|COMBAT SPEED|PASSENGERS|MANEUVER|THRUST|HULL)\s*\|",
    re.I,
)
# Weapon-type phrases that appear in equipment write-ups but not in rules prose.
_WEAPON_WORDS = re.compile(
    r"\b(assault rifle|sniper rifle|battle rifle|submachine gun|machine gun|"
    r"bolt-action|grenade launcher|shotgun|revolver|carbine)\b",
    re.I,
)


def _content_category(body: str) -> str | None:
    if _VEHICLE_TABLE.search(body):
        return "vehicles"
    if _WEAPON_TABLE.search(body) or _WEAPON_WORDS.search(body):
        return "items"
    return None


def _split_game_and_file(path: str) -> tuple[str, str]:
    if "/" in path:
        return path.split("/", 1)[0], path.rsplit("/", 1)[-1]
    return "General", path


def _clean(line: str) -> str:
    line = line.strip()
    if not line or _PICTURE.search(line) or _WATERMARK.search(line):
        return ""
    return line.replace("<br>", " ").replace("**", "").strip()


def _pages(text: str) -> list[tuple[str, str]]:
    """Ordered (page_label, page_text); one empty-label page if no markers."""
    if not _PAGE_MARKER.search(text):
        return [("", text)]
    parts = _PAGE_MARKER.split(text)
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def _heading(line: str) -> tuple[int, str] | None:
    """Return (level, title) if the line is a heading, else None."""
    m = _MD_HEADER.match(line)
    if m:
        return len(m.group(1)), m.group(2).replace("**", "").strip()
    m = _T2K_CHAPTER.match(line)
    if m:
        return 1, m.group(2).title().strip()  # ALL-CAPS -> Title Case
    return None


def _chunks_for_doc(path: str, text: str) -> list[Chunk]:
    game, book = _split_game_and_file(path)
    chapter = ""   # level-1 heading
    section = ""   # deeper heading
    entry = ""     # a single definition entry (e.g. one specialty / one item)
    buf: list[str] = []
    buf_words = 0
    buf_page = ""
    chunks: list[Chunk] = []
    # Per-chunk "chapter › section" key (no entry), so the stat-table refinement
    # below can still propagate a category across every entry in a section.
    sec_keys: list[str] = []

    def crumb() -> str:
        return " › ".join(p for p in (chapter, section, entry) if p)

    def flush() -> None:
        nonlocal buf, buf_words
        body = " ".join(buf).strip()
        if len(body.split()) >= 5:  # skip trivial fragments
            chunks.append(
                Chunk(
                    game=game,
                    source=book,
                    category=_category(book, chapter, section),
                    section=crumb(),
                    locator=f"p. {buf_page}" if buf_page else "",
                    text=body,
                )
            )
            sec_keys.append(" › ".join(p for p in (chapter, section) if p))
        buf = []
        buf_words = 0

    for page_label, page_text in _pages(text):
        for raw in page_text.splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            h = _heading(stripped)
            if h:
                flush()  # close the previous section's chunk
                level, title = h
                if level <= 1:
                    chapter, section = title, ""
                else:
                    section = title
                entry = ""  # a new heading ends any run of definition entries
                buf_page = page_label
                continue
            d = _DEF_ENTRY.match(stripped)
            if d:
                flush()  # close the previous entry / prose
                entry = d.group("term").strip()
                buf_page = page_label
                # Keep the term in the body so the entry reads on its own.
                body = _clean(d.group("body"))
                buf = [f"{entry}: {body}"] if body else []
                buf_words = sum(len(x.split()) for x in buf)
                continue
            line = _clean(raw)
            if not line:
                continue
            if not buf:
                buf_page = page_label
            buf.append(line)
            buf_words += len(line.split())
            if buf_words >= _TARGET_WORDS:
                flush()
    flush()

    # Refine: a section containing a weapon/vehicle stat table is gear/vehicles.
    # Propagate to every chunk in that section so the prose write-ups move too.
    section_cat: dict[str, str] = {}
    for ch, key in zip(chunks, sec_keys):
        sig = _content_category(ch.text)
        if not sig:
            continue
        if key:
            if section_cat.get(key) != "vehicles":  # vehicles wins ties
                section_cat[key] = sig
        else:
            ch.category = sig
    for ch, key in zip(chunks, sec_keys):
        if key and key in section_cat:
            ch.category = section_cat[key]

    # Keep the book's alphabetical index and leftover page-footer fragments out
    # of rule lookups: a single-letter section leaf, or a long number-dense chunk,
    # is reference clutter, not a rule. Retag so /rule|/item|/vehicle skip it.
    for ch in chunks:
        if ch.category != "rules":
            continue
        leaf = ch.section.split("›")[-1].strip()
        toks = ch.text.split()
        digit_frac = sum(t.isdigit() for t in toks) / len(toks) if toks else 0.0
        if (len(leaf) == 1 and leaf.isalpha()) or (
            digit_frac >= 0.30 and len(toks) >= 12
        ):
            ch.category = "reference"
    return chunks


def _tables_for_doc(path: str, text: str) -> list[Chunk]:
    """Recover OCR'd image-tables as their own searchable, renderable chunks.

    Scans headings, page markers, and picture-text blocks in document order so
    each table is labelled with the heading above it and the page it's on. Stored
    with category 'tables' and the raw rows kept for aligned rendering later.
    """
    game, book = _split_game_and_file(path)

    events: list[tuple[int, int, str, object]] = []
    for m in _SCAN_PAGE.finditer(text):
        events.append((m.start(), 0, "page", m.group(1)))
    for m in _SCAN_MD.finditer(text):
        events.append(
            (m.start(), 1, "head", (len(m.group(1)), m.group(2).replace("**", "").strip()))
        )
    for m in _SCAN_T2K.finditer(text):
        events.append((m.start(), 1, "head", (1, m.group(2).title().strip())))
    for m in _PIC_BLOCK.finditer(text):
        events.append((m.start(), 2, "block", m.group("body")))
    events.sort(key=lambda e: (e[0], e[1]))

    chapter = section = page = ""
    chunks: list[Chunk] = []
    for _pos, _pri, kind, data in events:
        if kind == "page":
            page = data  # type: ignore[assignment]
        elif kind == "head":
            level, title = data  # type: ignore[misc]
            if level <= 1:
                chapter, section = title, ""
            else:
                section = title
        else:  # block
            rows = parse_picture_rows(data)  # type: ignore[arg-type]
            if len(rows) < 2:
                continue  # not a table (caption, single label, etc.)
            crumb = " › ".join(p for p in (chapter, section) if p) or rows[0][:60]
            chunks.append(
                Chunk(
                    game=game,
                    source=book,
                    category="tables",
                    section=crumb,
                    locator=f"p. {page}" if page else "",
                    text="\n".join(rows),
                    rows=rows,
                )
            )
    return chunks


class RulesService:
    def __init__(self, drive: DriveClient | None) -> None:
        self.drive = drive
        self.index = SearchIndex()

    @property
    def ready(self) -> bool:
        return not self.index.is_empty

    def refresh(self) -> dict:
        """(Re)download from Drive and rebuild the index. Returns a summary."""
        if self.drive is None:
            raise RuntimeError("Google Drive is not configured.")
        docs = self.drive.fetch_all()
        chunks: list[Chunk] = []
        for doc in docs:
            chunks.extend(_chunks_for_doc(doc.name, doc.text))
            chunks.extend(_tables_for_doc(doc.name, doc.text))
        self.index.build(chunks)
        return {
            "documents": len(docs),
            "chunks": len(chunks),
            "games": self.index.files_by_game,
        }

    def search(
        self,
        query: str,
        game: str | None = None,
        book: str | None = None,
        category: str | None = None,
        top_k: int = 5,
    ) -> list[SearchHit]:
        return self.index.search(
            query, top_k=top_k, game=game, book=book, category=category
        )
