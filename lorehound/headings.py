"""ML-free heading detection for pymupdf4llm — bold/colour/caps aware.

pymupdf4llm's built-in ``IdentifyHeaders`` keys only on font **size**: the most
common size is body text, larger sizes become headings. That misses books whose
headings are the *same size* as body but set apart by **bold**, **colour**, or
**ALL CAPS** — on some rulebooks that means *zero* headings detected, leaving the
section-aware chunking nothing to work with.

``StyleHeadings`` scores each text style by size-over-body + bold + colour,
auto-calibrated per document, and assigns heading levels by rank. It implements
the same ``get_header_id(span, page)`` contract as ``IdentifyHeaders``, so it
drops into ``pymupdf4llm.to_markdown(doc, hdr_info=StyleHeadings(doc))``.

Wired into the extraction path: ``drive_client._pdf_markdown`` runs
``StyleHeadings`` → :func:`demote_noise_doc` → :func:`inject_toc_headings`.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import pymupdf  # PyMuPDF (fitz)

_BOLD_FLAG = 1 << 4  # PyMuPDF span "flags" bit for bold

# Strength weights. Bold is weighted heavily (research: bold correlates ~0.70 with
# heading-ness vs ~0.24 for the size flag); size still dominates when present.
_W_SIZE = 1.5   # per rounded point above body size
_W_BOLD = 4.0
_W_COLOR = 2.5

_FRAC_MAX = 0.18        # a larger-than-body heading style is rare (share of text)
_SAMESIZE_FRAC_MAX = 0.03  # a same-size BOLD heading style is rarer still
_MAX_LEVELS = 6
_MAX_WORDS = 8          # headings are short; longer = a styled table-header row
_MAX_LEN = 80           # …or this many characters
_MAX_SAMESIZE_LEN = 55  # a same-size (bold/colour) heading line is shorter still
_CAPS_RATIO = 0.6       # …or mostly UPPERCASE


def _is_bold(span: dict) -> bool:
    return bool(span["flags"] & _BOLD_FLAG) or "bold" in span.get("font", "").lower()


def _toks(s: str) -> set:
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if len(t) > 2}


def _caps_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(c.isupper() for c in letters) / len(letters)


class StyleHeadings:
    def __init__(self, doc, pages: list | None = None, max_levels: int = _MAX_LEVELS):
        mydoc = doc if isinstance(doc, pymupdf.Document) else pymupdf.open(doc)
        if pages is None:
            pages = range(mydoc.page_count)

        size_chars: dict[int, int] = defaultdict(int)
        color_chars: dict[int, int] = defaultdict(int)
        style_chars: dict[tuple, int] = defaultdict(int)  # (size, bold, color) -> chars
        total = 0
        for pno in pages:
            page = mydoc.load_page(pno)
            blocks = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]
            for b in blocks:
                for ln in b.get("lines", []):
                    for s in ln["spans"]:
                        t = s["text"].strip()
                        if not t:
                            continue
                        n = len(t)
                        size = round(s["size"])
                        key = (size, _is_bold(s), s.get("color", 0))
                        size_chars[size] += n
                        color_chars[s.get("color", 0)] += n
                        style_chars[key] += n
                        total += n
        if mydoc is not doc:
            mydoc.close()

        self.body_size = max(size_chars, key=size_chars.get) if size_chars else 12
        self.body_color = max(color_chars, key=color_chars.get) if color_chars else 0
        self._total = max(total, 1)

        # Score each style; keep the rare, distinctive ones as heading candidates.
        # Larger-than-body sizes qualify on size; same-size styles qualify only
        # when BOLD and rare (colour alone is too noisy — sidebars are coloured).
        scored: dict[tuple, float] = {}
        for (size, bold, color), chars in style_chars.items():
            colored = color != self.body_color
            frac = chars / self._total
            if size > self.body_size:
                if frac > _FRAC_MAX:
                    continue  # a big share at a larger size → really body text
            elif size == self.body_size and bold and frac < _SAMESIZE_FRAC_MAX:
                pass  # rare same-size bold → candidate
            else:
                continue  # smaller, or same-size non-bold (incl. coloured body)
            strength = (
                (size - self.body_size) * _W_SIZE
                + (_W_BOLD if bold else 0.0)
                + (_W_COLOR if colored else 0.0)
            )
            if strength > 0:
                scored[(size, bold, colored)] = strength

        # Bin ALL candidates into 1..max_levels by strength *rank* (quantile), so
        # mid-size section headings still get a level even when rare giant
        # decorative fonts exist — and outliers don't crowd them out.
        distinct = sorted(set(scored.values()), reverse=True)
        m = len(distinct) or 1
        level_of = {s: 1 + (i * max_levels) // m for i, s in enumerate(distinct)}
        self.levels: dict[tuple, int] = {k: level_of[v] for k, v in scored.items()}

    def get_header_id(self, span: dict, page=None) -> str:
        """Markdown header prefix ('# '..'###### ') for a span, or '' for body."""
        size = round(span["size"])
        bold = _is_bold(span)
        colored = span.get("color", 0) != self.body_color
        level = self.levels.get((size, bold, colored))
        if not level:
            return ""
        text = span["text"].strip()
        if len(text) <= 1:
            return ""  # empty, or a drop-cap initial — not a heading
        if len(text) > _MAX_LEN or len(text.split()) > _MAX_WORDS:
            return ""  # too long for a heading (e.g. a styled table-header row)
        if size <= self.body_size:
            # Same-size heading (bold/colour): guard against bold inline emphasis —
            # it must be short and either UPPERCASE or coloured.
            if len(text) > _MAX_SAMESIZE_LEN:
                return ""
            if not (colored or _caps_ratio(text) >= _CAPS_RATIO):
                return ""
        return "#" * level + " "


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_MARKUP_RE = re.compile(r"[*_`#]")
_MAX_REPEATS = 40  # doc-wide: a heading recurring more than this is a running header / repeated
# stat-block label (MAINTENANCE COST etc.). Tuned via threshold sweep: 40 strips the egregious
# spam (recurs 40+×) while sparing real repeated sections (per-alien "Careers" etc.) — only ~1
# real heading lost in a 120-sample, vs 9 lost at threshold 8. Recall-first.


def _is_pure_number(text: str) -> bool:
    """True for '83', '0.5', '+1', '–' — page numbers / stray values, never headings."""
    return not any(ch.isalpha() for ch in text)


def _heading_counts(md: str) -> Counter:
    c: Counter = Counter()
    for line in md.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            c[_MARKUP_RE.sub("", m.group(2)).strip().lower()] += 1
    return c


def demote_noise(md: str, *, max_repeats: int = _MAX_REPEATS, counts: Counter | None = None) -> str:
    """Demote false headings the per-span hook can't see — **pure-number** 'headings'
    (page numbers), **table-header rows** (too long / too many words), and **running
    headers / repeated stat-block labels** (recur > max_repeats). Demoted lines keep
    their text, minus the ``#`` prefix.

    Pass ``counts`` (from :func:`_heading_counts` over a whole document) to judge
    recurrence across all pages, not just within this string — see
    :func:`demote_noise_doc`.
    """
    counts = counts if counts is not None else _heading_counts(md)
    out = []
    for line in md.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            vis = _MARKUP_RE.sub("", m.group(2)).strip()
            if vis and (
                _is_pure_number(vis)
                or len(vis.split()) > _MAX_WORDS
                or len(vis) > _MAX_LEN
                or counts[vis.lower()] > max_repeats
            ):
                out.append(m.group(2))  # keep the text, drop the heading prefix
                continue
        out.append(line)
    return "\n".join(out)


def demote_noise_doc(page_texts: list[str], *, max_repeats: int = _MAX_REPEATS) -> list[str]:
    """:func:`demote_noise` across a whole document — recurrence counted over ALL pages,
    so a label appearing once per page (e.g. a ship's ``MAINTENANCE COST``) is seen as the
    running noise it is. Use this, not per-page ``demote_noise``."""
    counts: Counter = Counter()
    for t in page_texts:
        counts.update(_heading_counts(t))
    return [demote_noise(t, max_repeats=max_repeats, counts=counts) for t in page_texts]


_FRONT_MATTER = frozenset({
    "credits", "illustrations", "interior illustrations", "additional illustrations",
    "cover art", "cover artist", "additional art", "art direction", "graphic design",
    "layout", "author", "authors", "editor", "editors", "writer", "writers",
    "special thanks", "acknowledgements", "acknowledgments", "dedication",
    "playtesters", "playtesting", "proofreading", "proofreaders", "cartography",
    "contents", "table of contents",
})


def drop_frontmatter(md: str) -> str:
    """Demote front-matter credit labels (Credits, Illustrations, Cover Art, Author …)
    — they head names, not rules, and never occur as a real content section. Exact
    (whole-heading) match only, so "Ship Layout" / "Co-Author Rules" are untouched."""
    out = []
    for line in md.splitlines():
        m = _HEADING_RE.match(line)
        if m and _MARKUP_RE.sub("", m.group(2)).strip().lower() in _FRONT_MATTER:
            out.append(m.group(2))  # demote to plain text
            continue
        out.append(line)
    return "\n".join(out)


def dedup_dropcaps(md: str) -> str:
    """Drop drop-cap-mangled heading duplicates. A chapter whose big decorative
    initial split into its own span loses that letter (``NTRODUCTION``); the full
    title (``Introduction``, usually the injected ToC heading) is also present, so
    we drop the fragment when some other heading equals it plus one leading letter."""
    full = set()
    for line in md.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            full.add(_MARKUP_RE.sub("", m.group(2)).strip().lower())
    out = []
    for line in md.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            t = _MARKUP_RE.sub("", m.group(2)).strip().lower()
            if len(t) >= 4 and any(o[1:] == t for o in full if len(o) == len(t) + 1):
                continue  # fragment of a fuller heading (missing its drop-cap letter)
        out.append(line)
    return "\n".join(out)


def inject_toc_headings(doc, page_texts: list[str]) -> list[str]:
    """Prepend each ToC chapter heading to the top of its page chunk.

    Publisher chapter titles are often large text laid over images on chapter
    openers, which ``to_markdown`` drops before heading detection sees them — so
    font/style heuristics (and even span-matching ``TocHeaders``) miss them. The
    ToC has the title + page directly, so we inject it, skipping pages that
    already carry a matching heading near the top.  ``page_texts`` is the list of
    per-page Markdown (index = ToC page number − 1).
    """
    toc = doc.get_toc() if hasattr(doc, "get_toc") else []
    out = list(page_texts)
    for level, title, page in toc:
        idx = page - 1
        title = title.strip()
        if not (0 <= idx < len(out)) or not title:
            continue
        want = _toks(title)
        top = [
            _MARKUP_RE.sub("", l).strip()
            for l in out[idx].splitlines()[:6]
            if l.lstrip().startswith("#")
        ]
        if want and any(len(want & _toks(h)) / len(want) >= 0.6 for h in top):
            continue  # a matching heading is already there
        out[idx] = f"{'#' * min(level, 6)} {title}\n\n{out[idx]}"
    return out
