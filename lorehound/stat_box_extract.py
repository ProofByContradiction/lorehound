"""Recover the boxed spell/feat entries that pymupdf4llm scrambles on dense
multi-column pages (Pathfinder's Spells/Feats chapters).

pymupdf4llm linearises those pages *across* their 2-3 side-by-side stat boxes, so
the ``##### **NAME KIND LEVEL**`` headings never form and the entries are lost.
This reconstructs them geometrically: each box is announced by a heading in a
distinctive display font, so we detect those, carve out each box's column region
(down to the next heading, minus the outer chapter-tab margin), read its spans in
reading order line by line, and re-emit the ``##### **NAME KIND LEVEL**`` +
``**Field** value`` Markdown that :mod:`stat_boxes` already parses. The output is
appended to the page's Markdown at extraction time.

Self-gating: it fires only on pages that carry the box signature — a short
all-caps name sharing its line with a ``KIND N`` token — so other books/pages
produce nothing. The box-heading *font* is derived from that signature per page
rather than hardcoded, so any publisher's display font works (not just Paizo's
``GoodOT-CondBold``); the literal below is only the backward-compatible default.
"""

from __future__ import annotations

import re
from collections import Counter

# The default box-name heading font (Paizo's condensed display bold). Used only as
# a fallback / for direct helper calls; page_spell_boxes derives the actual font(s)
# from each page's own box signature via _detect_box_heads.
_HEAD_FONT = "GoodOT-CondBold"
_HEAD_SIZE_LO, _HEAD_SIZE_HI = 11.0, 13.0  # default display-heading size window
# Known Paizo box categories — always accepted so existing books are unchanged.
_KNOWN_KINDS = frozenset({"SPELL", "CANTRIP", "FOCUS", "RITUAL", "FEAT"})
_KIND_RE = re.compile(r"\b(SPELL|CANTRIP|FOCUS|RITUAL|FEAT)\s*(\d+)\b")
# The generic shape of a box's category+rank label: a ≥3-letter all-caps word then a
# number ("SPELL 1", "POWER 3"). ≥3 letters skips common 2-letter stats (HP/AC/DC),
# and a *novel* category must also recur (see _detect_kinds) so a stray "STR 10" or
# "AREA 20" never turns a prose page into boxes.
_KIND_TOKEN_RE = re.compile(r"\b([A-Z][A-Z’'\-]{2,})\s+(\d+)\b")
_MIN_NOVEL_KIND_RECURRENCE = 3
# Default page-chrome font fragment (Paizo prints the vertical chapter-tab margin and
# running headers in "Gin"). page_spell_boxes ALSO derives chrome fonts from the
# document's own ToC (see _derive_chrome), so this is just the backward-compatible seed.
_CHROME_FONT = "Gin"
# Fallback chapter-tab words (Paizo). When a document exposes a ToC, its chapter titles
# are folded in on top of these so the list isn't tied to one publisher's book.
_TAB_WORDS = frozenset({
    "Introduction", "Ancestries", "Backgrounds", "Classes", "Skills", "Feats",
    "Equipment", "Spells", "Game", "Playing", "Mastering", "Gamemastering",
    "Crafting", "Treasure", "Appendix", "Glossary", "Index", "Age", "Lost",
    "Omens", "Building", "Subsystems", "the", "of", "The", "&",
})


def _looks_like_box_name(text: str) -> bool:
    """A short, all-caps, alphabetic run — the shape of a box's NAME heading,
    independent of font (e.g. ``MAGIC MISSILE``). Digits disqualify it so the
    ``SPELL 1`` level label isn't mistaken for a name."""
    t = text.strip().rstrip("\t").strip()
    return (t.isupper() and len(t) >= 3
            and t.replace(" ", "").replace("-", "").replace("’", "").isalpha())


def _is_heading_span(s, fonts=(_HEAD_FONT,), size_lo=_HEAD_SIZE_LO, size_hi=_HEAD_SIZE_HI) -> bool:
    """Whether span ``s`` is a box-name heading. ``fonts``/``size_lo``/``size_hi``
    default to Paizo's signature so direct callers keep working, but
    :func:`page_spell_boxes` passes the font(s) and size band it *derived* from the
    page itself (see :func:`_detect_box_heads`) so no publisher font is hardcoded."""
    return (size_lo <= s["size"] <= size_hi and any(f in s["font"] for f in fonts)
            and _looks_like_box_name(s["text"]))


def _kind_to_right(span: dict, spans: list[dict], kind_re: re.Pattern) -> re.Match | None:
    """The ``KIND N`` token on ``span``'s visual line, to its right, if any — the
    marker that a short all-caps run is a box name rather than a section title.
    (The name and its KIND label are separate spans.)"""
    sx, sy = span["origin"]
    for o in spans:
        if o is span:
            continue
        if abs(o["origin"][1] - sy) < 6 and o["origin"][0] > sx:
            m = kind_re.search(o["text"].upper())
            if m:
                return m
    return None


def _detect_kinds(spans: list[dict]) -> set[str]:
    """The box-category words that anchor boxes on this page, derived from the layout
    rather than hardcoded to Paizo's. For every short all-caps name candidate, look
    right on its line for a generic ``<CAPWORD> <number>`` token and tally the capword.
    Known Paizo kinds count as soon as they appear (so existing books are unchanged);
    a *novel* category must recur (``_MIN_NOVEL_KIND_RECURRENCE``) so a one-off like a
    single ``STR 10`` never promotes a prose page into boxes. ``set()`` → no boxes here."""
    counts: Counter = Counter()
    for s in spans:
        if not _looks_like_box_name(s["text"]):
            continue
        m = _kind_to_right(s, spans, _KIND_TOKEN_RE)
        if m:
            counts[m.group(1)] += 1
    kinds = {k for k in counts if k in _KNOWN_KINDS}
    kinds |= {k for k, n in counts.items() if n >= _MIN_NOVEL_KIND_RECURRENCE}
    return kinds


def _kind_regex(kinds: set[str]) -> re.Pattern:
    """A ``\\b(K1|K2|…)\\s*(\\d+)\\b`` matcher for this page's derived box categories,
    used to pull each box's rank label and to strip stray KIND tokens from body text."""
    alt = "|".join(re.escape(k) for k in sorted(kinds, key=len, reverse=True))
    return re.compile(rf"\b({alt})\s*(\d+)\b")


def _detect_box_heads(
    spans: list[dict], kind_re: re.Pattern | None = None
) -> tuple[set[str], float, float]:
    """Derive the box-name heading font(s) and size band from the page's own box
    signature: a short all-caps run sharing its visual line with a ``KIND N`` token
    to its right (``MAGIC MISSILE`` … ``SPELL 1``). The fonts/sizes of the names that
    match become the page's heading style — so any display font works, not just
    ``GoodOT-CondBold``. ``kind_re`` defaults to the categories derived for this page
    (:func:`_detect_kinds`); callers that already derived it pass it in. Returns
    ``(set(), 0, 0)`` when the page carries no boxed entries, keeping it self-gating."""
    if kind_re is None:
        kinds = _detect_kinds(spans)
        if not kinds:
            return set(), 0.0, 0.0
        kind_re = _kind_regex(kinds)
    fonts: Counter = Counter()
    sizes: list[float] = []
    for s in spans:
        if _looks_like_box_name(s["text"]) and _kind_to_right(s, spans, kind_re):
            fonts[s["font"]] += 1
            sizes.append(s["size"])
    if not sizes:
        return set(), 0.0, 0.0
    return set(fonts), min(sizes) - 0.5, max(sizes) + 0.5


def _is_bold(s) -> bool:
    return bool(s["flags"] & 16) or "Bold" in s["font"] or "Semibold" in s["font"]


def _norm_text(t: str) -> str:
    return " ".join(t.strip().split()).casefold()


def toc_titles(doc) -> frozenset[str]:
    """The document's own chapter/section titles (from its embedded ToC), used to
    derive page chrome per :func:`_derive_chrome` instead of hardcoding one book's
    tab words. Empty when the book has no ToC — chrome then falls back to the Paizo
    defaults. Computed once per document by the caller and passed to
    :func:`page_spell_boxes`."""
    try:
        entries = doc.get_toc(simple=True)
    except Exception:  # noqa: BLE001 — a book with no/broken ToC just yields none
        return frozenset()
    return frozenset(t.strip() for _lvl, t, _pg in entries if t and t.strip())


def _derive_chrome(raw: list[dict], titles: frozenset[str]) -> tuple[set[str], set[str]]:
    """``(chrome_fonts, tab_words)`` for a page. Page chrome — the vertical chapter-tab
    margin and running headers — is set in a font used *only* for chrome, and its text
    is a chapter title. So any span whose text is a full ToC title is chrome, and its
    font is a chrome font; we drop that font's spans (which also catches sibling chrome
    like a ``LEVEL`` running header). The page body and box-heading fonts are excluded
    so real content is never dropped. Unions the Paizo defaults so a ToC-less book (or
    one whose running headers don't match) behaves exactly as before."""
    chrome_fonts = {_CHROME_FONT}
    tab_words = set(_TAB_WORDS)
    if not titles:
        return chrome_fonts, tab_words
    norm_titles = {_norm_text(t) for t in titles}
    chars: Counter = Counter()
    for s in raw:
        chars[s["font"]] += len(s["text"])
    body_font = chars.most_common(1)[0][0] if chars else ""
    head_fonts, _lo, _hi = _detect_box_heads(raw)
    for s in raw:
        font = s["font"]
        if (font != body_font and font not in head_fonts
                and _norm_text(s["text"]) in norm_titles):
            chrome_fonts.add(font)          # a running header / tab set in this font
    for t in titles:
        tab_words.update(w for w in t.split() if len(w) >= 2)
    return chrome_fonts, tab_words


def _is_chrome(s: dict, chrome_fonts: set[str]) -> bool:
    return any(cf in s["font"] for cf in chrome_fonts)


def _raw_spans(page) -> list[dict]:
    """All non-empty text spans on the page (chrome included — the caller filters)."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            out.extend(s for s in line["spans"] if s["text"].strip())
    return out


def _box_lines(spans: list[dict], kind_re: re.Pattern = _KIND_RE) -> list[str]:
    """Render region spans as Markdown lines (one per visual line), wrapping short
    bold spans as ``**label**`` so field labels survive. ``kind_re`` (this page's
    derived box categories) strips a stray rank label like ``SPELL 1`` from the body."""
    spans = sorted(spans, key=lambda s: (round(s["origin"][1] / 2.5), s["origin"][0]))
    lines: list[list[dict]] = []
    for s in spans:
        y = s["origin"][1]
        if lines and abs(y - lines[-1][0]["origin"][1]) <= 3:
            lines[-1].append(s)
        else:
            lines.append([s])
    out = []
    for ln in lines:
        parts = []
        for s in ln:
            t = s["text"].strip()
            if not t or kind_re.fullmatch(t.upper()):
                continue
            parts.append(f"**{t}**" if _is_bold(s) and len(t) <= 18 else t)
        text = re.sub(r"[ \t]+", " ", " ".join(parts)).strip()
        if text:
            out.append(text)
    return out


def page_spell_boxes(page, titles: frozenset[str] = frozenset()) -> str:
    """Reconstructed ``##### **NAME KIND LEVEL**`` box Markdown for one page, or "".

    ``titles`` are the document's ToC chapter titles (see :func:`toc_titles`), used to
    derive page chrome for this book rather than hardcoding Paizo's; empty falls back
    to the Paizo defaults."""
    width = page.rect.width
    mid = width / 2
    raw = _raw_spans(page)
    # Derive this book's page-chrome fonts / tab words from its ToC, then drop chrome
    # so the vertical tab margin and running headers don't bleed into a box's text.
    chrome_fonts, tab_words = _derive_chrome(raw, titles)
    spans = [s for s in raw if not _is_chrome(s, chrome_fonts)]
    # Derive this page's box categories (SPELL/FEAT, or a system we've never seen) and
    # the box-heading font(s) + size band from the page's own signature — so the
    # detector depends on neither a publisher's display font nor its category vocabulary.
    kinds = _detect_kinds(spans)
    if not kinds:
        return ""
    kind_re = _kind_regex(kinds)
    fonts, size_lo, size_hi = _detect_box_heads(spans, kind_re)
    if not fonts:
        return ""

    def is_head(s) -> bool:
        return _is_heading_span(s, fonts, size_lo, size_hi)

    heads = [s for s in spans if is_head(s)]
    if not heads:
        return ""

    def col(x: float) -> int:
        return 0 if x < mid else 1

    boxes: list[str] = []
    for h in heads:
        name = h["text"].strip().rstrip("\t").strip()
        hx, hy = h["origin"]
        c = col(hx)
        # level: a KIND+number span on the heading's line, to its right
        level = ""
        for s in spans:
            if abs(s["origin"][1] - hy) < 6 and s["origin"][0] > hx:
                m = kind_re.search(s["text"].upper())
                if m:
                    level = f"{m.group(1)} {m.group(2)}"
                    break
        if not level:
            continue  # an all-caps section title, not an entry
        # region: from this heading down to the next heading in the same column,
        # within the column's x-band (excluding the outer chapter-tab margin)
        below = sorted(s["origin"][1] for s in heads
                       if col(s["origin"][0]) == c and s["origin"][1] > hy + 4)
        ymax = below[0] if below else page.rect.height
        xlo, xhi = (36, mid - 6) if c == 0 else (mid + 2, width - 40)
        region = [s for s in spans
                  if xlo <= s["origin"][0] < xhi and hy + 4 <= s["origin"][1] < ymax - 2
                  and not is_head(s)
                  and s["text"].strip() not in tab_words]
        body = "\n".join(_box_lines(region, kind_re))
        boxes.append(f"##### **{name} {level}**\n{body}\n")
    return "\n".join(boxes)
