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

Prototype (deep-research finding #2). Not yet wired into the extraction path.
"""

from __future__ import annotations

from collections import defaultdict

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
_MAX_SAMESIZE_LEN = 60  # a same-size (bold/colour) heading line is short
_CAPS_RATIO = 0.6       # …or mostly UPPERCASE


def _is_bold(span: dict) -> bool:
    return bool(span["flags"] & _BOLD_FLAG) or "bold" in span.get("font", "").lower()


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
        distinct = sorted({v for v in scored.values()}, reverse=True)
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
        if size <= self.body_size:
            # Same-size heading (bold/colour): guard against bold inline emphasis —
            # it must be short and either UPPERCASE or coloured.
            if len(text) > _MAX_SAMESIZE_LEN:
                return ""
            if not (colored or _caps_ratio(text) >= _CAPS_RATIO):
                return ""
        return "#" * level + " "
