"""Glue between Google Drive and the search index.

Pulls documents, groups them by game (top-level Drive subfolder), parses their
(now Markdown) structure into heading-aware, categorized chunks, and answers
scoped searches. PyMuPDF extraction (in drive_client) yields Markdown whose
headings we use to (a) tag each chunk with a section breadcrumb and (b) classify
it as "rules" (character abilities/procedures) vs "stuff" (gear & vehicles).
"""

from __future__ import annotations

import re
import threading

from .careers import Career, assemble_career, detect_careers
from .drive_client import DriveClient
from .search_index import Chunk, SearchHit, SearchIndex, name_match_score, tokenize


class ReindexInProgress(RuntimeError):
    """Raised when refresh() is called while another refresh is already running.

    refresh() runs in a worker thread from two places (the startup warm and the
    operator's /reindex). A second concurrent call would duplicate the Drive pull
    and PDF extraction and clear the ``indexing`` flag out from under the first, so
    it's rejected rather than allowed to race."""

_PAGE_MARKER = re.compile(r"\[\[page (\d+)\]\]")
_MD_HEADER = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# Twilight 2000 page-top chapter line, e.g. "04 COMBAT & DAMAGE".
_T2K_CHAPTER = re.compile(r"^(\d{2})\s+([A-Z][A-Z0-9 &'/.\-]{2,})\s*$")
# PyMuPDF placeholders/markers to drop from body text.
_PICTURE = re.compile(r"==>.*omitted.*<==|----- (Start|End) of picture text -----")
# Free League PDF watermark footer leaks into the text (e.g. "0 75 Adam Delaura
# (Order #51743052)"); drop any line carrying the order-watermark.
_WATERMARK = re.compile(r"\(Order #\d+\)")

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

# ── Content-classification vocabulary ───────────────────────────────────────
# Three layers route a chunk into rules / items / transport, each matching a
# different view of the same page. The keyword families live here once; the
# matchers below compose them, each with its own delimiters:
#   • HEADING — _ITEM / _VEHICLE, run over book name + chapter/section headings
#     ONLY (not body), so a rule that merely *mentions* a weapon stays a rule.
#     Vehicles take precedence over items (e.g. "ship weapon").
#   • COLUMN  — _WEAPON_TABLE / _VEHICLE_TABLE, run over a Markdown stat-table's
#     column names, to re-tag a weapon/vehicle block hiding under a rules
#     heading (e.g. a T2K weapon write-up in the Combat chapter). Real rules
#     never carry these columns.
#   • PROSE   — _WEAPON_WORDS, weapon-type phrases that appear in equipment
#     write-ups but never in rules prose.
# A fourth layer — reconstructed cell grids, not Markdown — lives in
# pdf_tables.classify_table (header-keyword routing for structured tables).


def _alt(*terms: str) -> str:
    """Regex alternation body from literal terms or sub-patterns (e.g. 'armou?r')."""
    return "|".join(terms)


# HEADING layer.
_VEHICLE_HEADINGS = (
    "vehicle", "vehicles", "ship", "ships", "starship", "spacecraft",
    "small craft", "high guard", "adventure class", "aircraft", "watercraft",
    "boat", "tank", "mecha", "hull", "thruster", "spinal mount", "turret",
)
_ITEM_HEADINGS = (
    "gear", "equipment", "equip", "weapon", "weapons", "armou?r", "supply",
    "catalog", "catalogue", "item", "items", "kit", "robot", "robots", "drone",
    "drones", "gadget", "loadout", "trade good", "cybernetic", "augment",
    "firearm", "rifle", "pistol", "shotgun", "grenade", "ammunition", "melee",
)
_VEHICLE = re.compile(rf"\b(?:{_alt(*_VEHICLE_HEADINGS)})\b", re.I)
_ITEM = re.compile(rf"\b(?:{_alt(*_ITEM_HEADINGS)})\b", re.I)

# COLUMN layer — Markdown stat-table column names.
_WEAPON_COLUMNS = ("ROF", "WEAPON")
_VEHICLE_COLUMNS = (
    "TRAVEL SPEED", "COMBAT SPEED", "PASSENGERS", "MANEUVER", "THRUST", "HULL",
)
_WEAPON_TABLE = re.compile(rf"\|\s*(?:{_alt(*_WEAPON_COLUMNS)})\s*\|", re.I)
_VEHICLE_TABLE = re.compile(rf"\|\s*(?:{_alt(*_VEHICLE_COLUMNS)})\s*\|", re.I)

# PROSE layer — weapon-type phrases.
_WEAPON_TYPES = (
    "assault rifle", "sniper rifle", "battle rifle", "submachine gun",
    "machine gun", "bolt-action", "grenade launcher", "shotgun", "revolver",
    "carbine",
)
_WEAPON_WORDS = re.compile(rf"\b(?:{_alt(*_WEAPON_TYPES)})\b", re.I)


# Topic buckets for grouping the /table list when browsing. Generic TTRPG topics
# matched on the table NAME first (most reliable — a "RADIATION SICKNESS" table
# lives under a "Combat & Damage" chapter but is Health), then the chapter. Order
# matters: the first topic whose keyword is found wins. This is a *browsing*
# taxonomy (deliberately stemmed — "forag", "armou", "thrust" — for prefix hits),
# distinct from the routing vocabulary above; the two are not interchangeable.
_TABLE_TOPICS: list[tuple[str, tuple[str, ...]]] = [
    ("Health", ("disease", "radiat", "sick", "heal", "medical", "surger", "trauma",
                "poison", "drug", "infect", "fatigue", "blister")),
    ("Character", ("character", "player", "attribute", "skill", "specialt",
                   "career", "background", "aging", "education", "talent", "qualif",
                   "advancement", "muster", "rank", "base dice", "success",
                   "archetype", "life path", "trait")),
    ("Travel", ("travel", "terrain", "forag", "hunt", "encumbr", "movement", "jump",
                "fuel", "navigat", "mishap", "camp", "driving", "weather", "light",
                "climate", "distance", "journey")),
    ("Vehicles", ("vehicle", "ship", "craft", "hull", "thrust", "spacecraft",
                  "high guard", "turret", "spinal", "mount")),
    ("Combat", ("combat", "damage", "hit", "attack", "fire", "melee", "initiative",
                "recoil", "autofire", "blast", "penetrat", "action", "ambush",
                "deviation", "barrier", "location", "chemical", "area of effect")),
    ("Gear", ("gear", "equipment", "weapon", "armou", "ammo", "supply", "price",
              "cost", "kit", "reliab", "radio", "firearm", "smg", "rifle", "pistol",
              "catalog", "quantit", "tool")),
    ("World", ("encounter", "reaction", "patron", "rumour", "animal", "trade",
               "cargo", "sector", "system", "government", "starport", "planet")),
]
# A chapter that's a filename / page artefact, not a real topic.
_NON_TOPIC = re.compile(r"^(pdf|page|\d+|.*\.pdf)$", re.I)

# Match a keyword at a word boundary (prefix), so "forag" hits "foraging" but
# "aging" does NOT match "for-aging" — substring matching mis-grouped tables.
_TABLE_TOPIC_RES = [
    (topic, re.compile(r"\b(" + "|".join(words) + ")", re.I))
    for topic, words in _TABLE_TOPICS
]


def _topic_match(text: str) -> str | None:
    for topic, rx in _TABLE_TOPIC_RES:
        if rx.search(text):
            return topic
    return None


def table_topic(chapter: str, name: str) -> str:
    """Group a rules table under a browsing topic (Combat / Health / Character /
    Travel / Vehicles / Gear / World). The table name decides it where possible,
    else the chapter; failing both, the cleaned chapter, else "Other"."""
    return (
        _topic_match(name)
        or _topic_match(chapter)
        or (chapter.split(".", 1)[-1].strip() if chapter and not _NON_TOPIC.match(chapter.strip()) else "Other")
    )


# Character-creation chapters: their "GEAR" / "AMMUNITION" / "ENCUMBRANCE" sub-
# sections are chargen *rules* (how to record/choose gear), not an item catalogue,
# and their prose mentions weapons ("…Assault rifle…") — so the gear/weapon
# signals below would wrongly route them to /item. Guarded as rules.
_CHARGEN = re.compile(
    r"\b(player character|character (?:creation|generation)|traveller creation|"
    r"life ?path|childhood|archetype)",
    re.I,
)


def _is_chargen(*texts: str) -> bool:
    """True if any of ``texts`` (a chapter/book/section name) marks character-
    creation content. Single definition of the chargen guard, shared by the
    build-time classifier and the post-pass that protects chargen prose from the
    body-driven gear/vehicle re-tag."""
    return any(_CHARGEN.search(t or "") for t in texts)


def _category(book: str, chapter: str, section: str) -> str:
    """Classify as 'rules', 'items', or 'transport' from the most specific heading."""
    if _is_chargen(chapter, book):
        return "rules"  # character-creation content is chargen rules, not gear
    for text in (section, chapter, book):
        if not text:
            continue
        if _VEHICLE.search(text):
            return "transport"
        if _ITEM.search(text):
            return "items"
    return "rules"


def _content_category(body: str) -> str | None:
    if _VEHICLE_TABLE.search(body):
        return "transport"
    if _WEAPON_TABLE.search(body) or _WEAPON_WORDS.search(body):
        return "items"
    return None


def _split_game_and_file(path: str) -> tuple[str, str]:
    if "/" in path:
        return path.split("/", 1)[0], path.rsplit("/", 1)[-1]
    return "General", path


def _strip_md(s: str) -> str:
    """Drop the inline markdown that leaks from PDF extraction into titles/body —
    bold ``**`` and strikethrough ``~~`` (the latter is how worked-example banners
    render, e.g. ``~~**EXAMPLE**~~``)."""
    return s.replace("~~", "").replace("**", "")


# A worked-example banner. T2K renders these struck-through (``~~EXAMPLE~~``); the
# heading word may be just "EXAMPLE" or "EXAMPLE: <name>" / "Worked example".
_EXAMPLE_HEADING = re.compile(r"^(worked\s+)?example\b", re.I)


def _is_example_heading(raw: str, title: str) -> bool:
    """True if a heading is a worked-example banner — recognised by the "example"
    word or by strikethrough on a short heading. Such a chunk is kept UNDER its
    parent section (as an "Example" leaf) rather than letting the banner replace the
    breadcrumb, so the example inherits the topic it illustrates."""
    return bool(_EXAMPLE_HEADING.match(title) or ("~~" in raw and len(title.split()) <= 5))


def _clean(line: str) -> str:
    line = line.strip()
    if not line or _PICTURE.search(line) or _WATERMARK.search(line):
        return ""
    return _strip_md(line.replace("<br>", " ")).strip()


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
        return len(m.group(1)), _strip_md(m.group(2)).strip()
    m = _T2K_CHAPTER.match(line)
    if m:
        return 1, m.group(2).title().strip()  # ALL-CAPS -> Title Case
    return None


def _chunks_for_doc(path: str, text: str) -> list[Chunk]:
    from .headings import dedup_dropcaps, drop_frontmatter

    text = drop_frontmatter(dedup_dropcaps(text))  # drop drop-cap frags + credit labels
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
                if _is_example_heading(stripped, title):
                    # Keep the parent section; file the example beneath it so it
                    # inherits the topic it illustrates (instead of an orphan crumb).
                    entry = "Example"
                elif level <= 1:
                    chapter, section, entry = title, "", ""
                else:
                    section, entry = title, ""
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

    # Post-classification refinement runs as ordered stages over the built chunks.
    # The order is load-bearing: the body-driven re-tag can promote a chunk to
    # items/transport, so the chargen guard must run AFTER it to claw chargen prose
    # back, and reference clutter is judged last (only over what's still 'rules').
    _retag_by_content(chunks, sec_keys)
    _guard_chargen(chunks)
    _retag_reference_clutter(chunks)
    return chunks


def _retag_by_content(chunks: list[Chunk], sec_keys: list[str]) -> None:
    """Stage 1 — a section containing a weapon/vehicle stat table is gear/vehicles.
    Propagate the body signal to every chunk sharing the section key, so the prose
    write-ups move with it (transport wins ties); keyless chunks retag in place."""
    section_cat: dict[str, str] = {}
    for ch, key in zip(chunks, sec_keys, strict=True):
        sig = _content_category(ch.text)
        if not sig:
            continue
        if key:
            if section_cat.get(key) != "transport":  # transport wins ties
                section_cat[key] = sig
        else:
            ch.category = sig
    for ch, key in zip(chunks, sec_keys, strict=True):
        if key and key in section_cat:
            ch.category = section_cat[key]


def _guard_chargen(chunks: list[Chunk]) -> None:
    """Stage 2 — character-creation prose must not land in /item or /transport even
    when stage 1 flagged it (its gear/weapon mentions are chargen, not a catalogue)
    — force it back to rules."""
    for ch in chunks:
        chapter = ch.section.split("›")[0].strip() if ch.section else ""
        if ch.category in ("items", "transport") and _is_chargen(chapter):
            ch.category = "rules"


def _retag_reference_clutter(chunks: list[Chunk]) -> None:
    """Stage 3 — keep the book's alphabetical index and leftover page-footer
    fragments out of rule lookups: a single-letter section leaf, or a long
    number-dense chunk, is reference clutter, not a rule. Retag (over chunks still
    'rules') so /rule|/item|/transport skip it."""
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


def _table_name(title: str, section: str, rows: list[list[str]]) -> str:
    """A clean display name for a table: the detected heading, else the TOC
    section, else the header row's first cell."""
    t = (title or "").strip()
    prose = (
        not t or t[:1].islower() or t.endswith((".", ",", ";")) or len(t.split()) > 6
    )
    if not prose:
        return t
    leaf = (section or "").split("›")[-1].strip()
    if leaf:
        return leaf
    if rows and rows[0]:
        first = next((c.strip() for c in rows[0] if c.strip()), "")
        if first:
            return first[:40]
    return "Table"


def _is_real_table(rows: list[list[str]]) -> bool:
    """Reject diagram-like grids that find_tables mis-detects as tables — e.g. the
    numbers scattered around a hit-location / firing-arc figure. A real rules/gear
    table has actual word content, not just stray digits/single characters."""
    cells = [(c or "").strip() for r in rows for c in r]
    nonempty = [c for c in cells if c]
    if len(nonempty) < 4:
        return False
    empty_frac = 1 - len(nonempty) / len(cells)
    wordish = sum(1 for c in nonempty if sum(ch.isalpha() for ch in c) >= 3) / len(nonempty)
    # Diagram garbage = a sparse grid with almost no words (digits scattered around a
    # figure). Real number/code tables (die types, %s, damage) stay dense, so require
    # BOTH sparsity and near-zero text before rejecting.
    return not (empty_frac > 0.35 and wordish < 0.25)


def _tables_for_doc(path: str, tables: list[dict]) -> list[Chunk]:
    """Build chunks from the structured tables recovered in drive_client.

    Each table dict carries page/chapter/section/title/category/rows. We route it
    by category — rules→'tables', items→'items', transport→'transport',
    card→'card' — tag it with a "Chapter › Name" breadcrumb, and keep the cell
    grid for aligned rendering.
    """
    from . import sources
    from .pdf_tables import classify_table

    game, book = _split_game_and_file(path)
    profile = sources.profile_for(game)  # supplies chapter-fallback routing
    cat_map = {
        "rules": "tables",
        "items": "items",
        "transport": "transport",
        "card": "card",
    }
    chunks: list[Chunk] = []
    for t in tables:
        rows = t.get("rows") or []
        if profile:  # repair a mis-segmented catalogue grid (e.g. PF armor) before routing
            rows = profile.normalize_rows(rows)
        if len(rows) < 2 or not _is_real_table(rows):
            continue
        name = _table_name(t.get("title", ""), t.get("section", ""), rows)
        chapter = (t.get("chapter") or "").strip()
        section = f"{chapter} › {name}" if chapter else name
        flat = "\n".join(" ".join(c for c in r if c) for r in rows)
        # Re-run classification at index time (not the cached category) so routing
        # fixes apply on a rebuild without re-extracting every PDF.
        category = classify_table(chapter, rows, profile)
        if category == "noise":
            continue
        chunks.append(
            Chunk(
                game=game,
                source=book,
                category=cat_map.get(category, "tables"),
                section=section,
                locator=f"p. {t['page']}" if t.get("page") else "",
                text=f"{name}\n{flat}",
                rows=rows,
            )
        )
    return chunks


def _is_catalog_name(name: str) -> bool:
    """True if `name` reads like an item/vehicle name rather than a rules-table
    row that leaked in (a dice result, a percentage outcome, a full sentence)."""
    if not 2 <= len(name) <= 40:
        return False
    low = name.lower()
    if "%" in name or name.endswith("."):
        return False
    if re.match(r"^\d", name):  # "10,", "1DD", "4D", "2D% of crew ..."
        return False
    if re.fullmatch(r"(?i)tl\s*\d+", name.strip()):  # "TL12" — an unnamed ship's tech level
        return False
    if re.search(r"\d{4,}", name):  # cost totals / serials ("Total: MCr 299798.6"), not names
        return False
    if any(w in low for w in ("suffer", "reduced by", "destroyed", "checks to", " takes ", " dm-", " dm+")):
        return False
    if len(name.split()) > 6:  # a catalogue name is a noun phrase, not a sentence
        return False
    return True


def _build_catalog_names(chunks: list[Chunk]) -> dict[tuple[str, str], list[str]]:
    """Per (game, category) sorted list of item names from catalog tables (wide
    stat blocks — weapons, vehicles), so /item and /transport can offer them in
    autocomplete. Only wide multi-row tables qualify (a rules/feature table isn't
    a catalogue)."""
    from collections import defaultdict

    from .tables import _SHIP_PARTS, _name_col, is_ship_statblock

    # Traveller renders each ship as a per-ship table whose *rows* are its
    # components (Hull, Bridge, M-Drive…) and whose name lives in the title, not
    # a column — so the name column yields ship systems, not vehicles. Drop those
    # stable system terms so they don't masquerade as vehicles in /transport.
    # Reuse the statblock-detection vocabulary (single source of truth) plus a few
    # catalogue-leak phrasings that only show up in the name column.
    ship_parts = _SHIP_PARTS | {
        "cargo the", "common cargo", "computer software systems", "bulkheads",
        "ammunition", "acceleration bench", "maintenance purchase", "tl7",
        "heavy", "light", "craft",
    }

    names: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for c in chunks:
        if c.category not in ("items", "transport") or not c.rows:
            continue
        rows = [[(x or "").strip() for x in r] for r in c.rows if any((x or "").strip() for x in r)]
        # A ship stat block's rows are its components, not a list of items — the
        # ship's name is the chunk heading, so emit that single name and move on.
        if c.category == "transport" and is_ship_statblock(rows):
            ship = c.section.split("›")[-1].strip()
            if _is_catalog_name(ship):
                names[(c.game, c.category)].setdefault(ship.lower(), ship)
            continue
        if len(rows) < 3 or len(rows[0]) < 4:
            continue
        nc = _name_col(rows)
        for r in rows[1:]:
            name = (r[nc].strip().rstrip(" *†‡").strip()) if nc < len(r) else ""
            name = re.sub(r"(\w)- (\w)", r"\1\2", name)  # de-hyphenate "Chal- lenger"
            # Skip pure-uppercase words — those are column headers that leaked in
            # from a fragment table (MAIN WEAPON, REAR), not item names.
            if not _is_catalog_name(name) or (name.replace(" ", "").isalpha() and name.isupper()):
                continue
            if c.category == "transport" and name.lower() in ship_parts:
                continue  # a ship system (Hull, Bridge…), not a vehicle
            names[(c.game, c.category)].setdefault(name.lower(), name)
    return {k: sorted(v.values()) for k, v in names.items()}


def _is_card_title(s: str) -> bool:
    """A table title that reads like a single item's name (T2K ``M1911A1``) — lenient
    where :func:`_is_catalog_name` is strict, since a #66-curated title isn't a catalog
    *row* value, so the cost-serial / leading-digit guards don't apply. Still rejects
    sentences, percentages and trailing-period prose."""
    s = s.strip()
    if not (2 <= len(s) <= 40) or s.endswith(".") or "%" in s:
        return False
    if not any(ch.isalpha() for ch in s) or len(s.split()) > 8:
        return False
    low = s.lower()
    return not any(w in low for w in ("suffer", "reduced by", "destroyed", "checks to", " takes "))


def _catalog_cards_for_chunk(c: Chunk):
    """Yield ``(name, rows)`` renderable single-item cards from a catalog/stat-block
    chunk, so an item name can be resolved straight to its card (BM25 buries a single
    item among a 26-row catalog below the relevance floor — see /item retrieval bug).

    Two shapes: a *multi-item catalog* (≥2 distinct item names in the name column,
    e.g. Traveller/Pathfinder weapon lists) yields one ``(name, [header, row])`` per
    row; a *single-item card* (the name lives in the title, not a column — Traveller
    ship stat blocks and T2K weapon cards) yields one ``(title-leaf, whole-table)``."""
    from .tables import _name_col, is_ship_statblock

    rows = [[(x or "").strip() for x in r] for r in (c.rows or [])
            if any((x or "").strip() for x in r)]
    leaf = c.section.split("›")[-1].strip()

    def _single():
        # A /transport card named only by its page heading must be a real component
        # stat block (a ship); otherwise it's a mis-routed fragment (a Crew list, a
        # Deckplan Legend, a power-requirements table) that leaked in — drop it. Real
        # vehicles come through the catalogue (multi-item) path, not here.
        if c.category == "transport" and not is_ship_statblock(rows):
            return []
        return [(leaf, c.rows)] if _is_card_title(leaf) else []

    if not rows or is_ship_statblock(rows) or len(rows) < 3 or len(rows[0]) < 4:
        yield from _single()
        return
    nc = _name_col(rows)
    header = rows[0]
    items: list[tuple[str, list[str]]] = []
    for r in rows[1:]:
        name = (r[nc].strip().rstrip(" *†‡").strip()) if nc < len(r) else ""
        name = re.sub(r"(\w)- (\w)", r"\1\2", name)  # de-hyphenate "Chal- lenger"
        if not _is_catalog_name(name) or (name.replace(" ", "").isalpha() and name.isupper()):
            continue
        items.append((name, r))
    if len({n.lower() for n, _ in items}) >= 2:
        for name, r in items:
            yield name, [header, r]          # genuine multi-item catalog
    else:
        yield from _single()                 # single-item stat block named by its title


def _build_catalog_cards(chunks: list[Chunk]) -> dict[tuple[str, str], list[tuple[str, Chunk]]]:
    """Per (game, category) list of ``(name, card_chunk)`` for every catalog item, so
    /item and /transport can resolve an item name directly to its Stat|Value card
    instead of relying on BM25 (which can't surface a single row of a long catalog)."""
    from collections import defaultdict

    # name_lower -> (name, card, from_catalog_row). ``from_catalog_row`` marks a clean
    # ``[header, row]`` card from a multi-item catalogue; a heading-named whole-table
    # statblock is not. When the same item appears as both — a T2K vehicle has a
    # mangled featured sidebar *and* a clean catalogue row — keep the catalogue row.
    out: dict[tuple[str, str], dict[str, tuple[str, Chunk, bool]]] = defaultdict(dict)
    for c in chunks:
        if c.category not in ("items", "transport") or not c.rows:
            continue
        leaf = c.section.split("›")[-1].strip()
        for name, rows in _catalog_cards_for_chunk(c):
            whole = rows is c.rows
            from_catalog_row = not whole
            existing = out[(c.game, c.category)].get(name.lower())
            # keep the first, but let a clean catalogue row replace a heading-named statblock
            if existing is not None and not (from_catalog_row and not existing[2]):
                continue
            card = Chunk(
                game=c.game, source=c.source, category=c.category,
                section=c.section if whole else f"{leaf} › {name}",
                locator=c.locator, text=name, rows=rows,
            )
            out[(c.game, c.category)][name.lower()] = (name, card, from_catalog_row)
    return {k: [(n, card) for n, card, _ in v.values()] for k, v in out.items()}


_SMALL_WORDS = {"of", "the", "and", "in", "to", "a", "an", "vs", "from",
                "with", "on", "at", "by", "for", "or", "into", "your"}


def _titlecase(s: str) -> str:
    """Title-case a book's ALL-CAPS entry name ("CLOAK OF COLORS" → "Cloak of
    Colors"), keeping small connecting words lower except in the first position."""
    words = s.split()
    return " ".join(
        w.capitalize() if i == 0 or w.lower() not in _SMALL_WORDS else w.lower()
        for i, w in enumerate(words)
    )


_STAT_BOX_GROUP = {"FEAT": "Feats", "SPELL": "Spells", "CANTRIP": "Spells",
                   "FOCUS": "Focus Spells", "RITUAL": "Rituals"}


def _stat_box_chunks_for_doc(path: str, text: str) -> list[Chunk]:
    """Build spell/feat cards from the boxed entries in a book's extracted markdown
    (see :mod:`stat_boxes`). Each box → one searchable card chunk: the level + fields
    as a label/value grid, the prose description carried in ``Chunk.description``."""
    from .stat_boxes import parse_stat_boxes
    from .text_utils import repair_ligatures

    game, book = _split_game_and_file(path)
    chunks: list[Chunk] = []
    for box in parse_stat_boxes(text):
        name = repair_ligatures(_titlecase(box.name))
        group = _STAT_BOX_GROUP.get(box.kind, "Spells")
        rows = [["Level", str(box.level)]] + [[lbl, val] for lbl, val in box.fields]
        flat = " ".join(val for _, val in box.fields)
        chunks.append(Chunk(
            game=game, source=book, category=box.category,
            section=f"{group} › {name}",
            locator=f"p. {box.page}" if box.page else "",
            text=f"{name} {box.kind.title()} {flat} {box.description}".strip(),
            rows=rows, description=box.description,
        ))
    return chunks


def _build_stat_cards(chunks: list[Chunk]) -> dict[tuple[str, str], list[tuple[str, Chunk]]]:
    """Per (game, category) name→card list for stat-box chunks (spell/feat), so
    /spell and /feat resolve a name straight to its card (same shape as the catalog
    cards, reusing catalog_card_lookup + catalog_names). Each box is its own card."""
    from collections import defaultdict

    out: dict[tuple[str, str], dict[str, tuple[str, Chunk]]] = defaultdict(dict)
    for c in chunks:
        if c.category in ("spell", "feat") and c.rows:
            name = c.section.split("›")[-1].strip()
            out[(c.game, c.category)].setdefault(name.lower(), (name, c))
    return {k: list(v.values()) for k, v in out.items()}


class RulesService:
    def __init__(self, drive: DriveClient | None) -> None:
        self.drive = drive
        self.index = SearchIndex()
        # Structured careers per game: {game -> {name_lower -> Career}}. Built at
        # index time; games absent here fall back to search-assemble in find_career.
        self.careers: dict[str, dict[str, Career]] = {}
        # Catalog item names per (game, category) for /item and /transport pickers.
        self._catalog: dict[tuple[str, str], list[str]] = {}
        # Renderable per-item cards per (game, category), so /item resolves a name
        # straight to its stat card (BM25 can't surface one row of a long catalog).
        self._catalog_cards: dict[tuple[str, str], list[tuple[str, Chunk]]] = {}
        # Per-game chargen aux data parsed from document prose at index time (e.g.
        # the T2K childhood table), for flows that need tables find_tables can't see.
        self.chargen_aux: dict[str, dict] = {}
        # True while refresh() is rebuilding the index (cold-start warm or /reindex).
        # The prior index stays live and queryable throughout; this only lets the UI
        # warn that data may change shortly and gate flows (chargen) that want a
        # stable snapshot. See ``ready`` / ``indexing``.
        self._indexing = False
        # Held for the duration of a refresh so concurrent calls (startup warm vs
        # /reindex, or a double-fired /reindex) are rejected, not run in parallel.
        self._refresh_lock = threading.Lock()

    @property
    def ready(self) -> bool:
        """True once a non-empty index exists (queryable). Stays True across a
        re-index — the old index is swapped out only when the new one is built."""
        return not self.index.is_empty

    @property
    def indexing(self) -> bool:
        """True while a refresh is downloading/extracting/building. Reads still work
        (old index is live); a feature wanting a stable view should wait."""
        return self._indexing

    def refresh(self, force: bool = False) -> dict:
        """(Re)download from Drive and rebuild the index. Returns a summary.

        ``force=True`` re-extracts every file from scratch (ignores the cache),
        for picking up changed extraction code without a version bump.

        The new index/careers/catalog are built off to the side and swapped in
        atomically at the end, so concurrent readers always see a complete index —
        either the old one or the new one, never a half-built mix."""
        if self.drive is None:
            raise RuntimeError("Google Drive is not configured.")
        from .chargen.registry import chargen_for

        # Re-entry guard: a non-blocking acquire fails if a refresh is already in
        # flight, so the duplicate is rejected instead of racing the live one.
        if not self._refresh_lock.acquire(blocking=False):
            raise ReindexInProgress("A reindex is already in progress.")
        self._indexing = True
        try:
            docs = self.drive.fetch_all(force=force)
            chunks: list[Chunk] = []
            aux: dict[str, dict] = {}
            for doc in docs:
                chunks.extend(_chunks_for_doc(doc.name, doc.text))
                chunks.extend(_tables_for_doc(doc.name, doc.tables))
                chunks.extend(_stat_box_chunks_for_doc(doc.name, doc.text))
                # Parse any prose-only chargen tables (e.g. T2K childhood) for games
                # with a chargen system, so the flow can read them from the index.
                game, _book = _split_game_and_file(doc.name)
                system = chargen_for(game)
                if system is not None and system.extract_prose is not None:
                    parsed = system.extract_prose(doc.text, doc.tables)
                    if parsed:
                        aux.setdefault(game, {}).update(parsed)
            index = SearchIndex()
            index.build(chunks)
            careers = detect_careers(chunks)
            catalog = _build_catalog_names(chunks)
            catalog_cards = _build_catalog_cards(chunks)
            # Stat-box cards (spell/feat) reuse the catalog card/name machinery so
            # /spell and /feat resolve a name straight to its card like /item does.
            stat_cards = _build_stat_cards(chunks)
            catalog_cards.update(stat_cards)
            for key, cards in stat_cards.items():
                catalog[key] = sorted(name for name, _ in cards)
            # Atomic swap: reference assignment is safe under the GIL, so a reader on
            # the event-loop thread sees the old index until this point, then the new.
            self.index = index
            self.careers = careers
            self._catalog = catalog
            self._catalog_cards = catalog_cards
            self.chargen_aux = aux
            return {
                "documents": len(docs),
                "chunks": len(chunks),
                "games": self.index.files_by_game,
                "careers": sum(len(v) for v in self.careers.values()),
            }
        finally:
            self._indexing = False
            self._refresh_lock.release()

    def catalog_names(self, game: str, category: str) -> list[str]:
        """Sorted item names for a game's weapon/vehicle catalogs (autocomplete)."""
        return self._catalog.get((game, category), [])

    def catalog_card_lookup(
        self, game: str, category: str, query: str, book: str | None = None
    ) -> list[SearchHit]:
        """Resolve an item name directly to its catalog card(s), bypassing BM25.

        Cards are scored by :func:`name_match_score` (the same scale the explode
        fallback uses) and returned highest-first; non-matches are dropped."""
        if not tokenize(query):
            return []
        out: list[SearchHit] = []
        for name, card in self._catalog_cards.get((game, category), []):
            if book and card.source != book:
                continue
            score = name_match_score(query, name)
            if score > 0:
                out.append(SearchHit(chunk=card, score=score))
        out.sort(key=lambda h: h.score, reverse=True)
        return out

    def search(
        self,
        query: str,
        game: str | None = None,
        book: str | None = None,
        category: str | None = None,
        top_k: int = 5,
        min_rel: float | None = None,
    ) -> list[SearchHit]:
        return self.index.search(
            query, top_k=top_k, game=game, book=book, category=category, min_rel=min_rel
        )

    # --- Careers (/class) ---------------------------------------------------

    def career_names(self, game: str) -> list[str]:
        """Structured career names for a game (for autocomplete); [] if none."""
        return sorted(c.name for c in self.careers.get(game, {}).values())

    def find_career(self, game: str, name: str) -> Career | None:
        """A career by name: the structured card if we have one, else a generic
        search-assembled card from the game's indexed career content."""
        structured = self.careers.get(game, {}).get(name.strip().lower())
        if structured is not None:
            return structured
        return assemble_career(self.search, game, name)
