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

Self-gating: it fires only where the heading font signature exists (Paizo's
``GoodOT-CondBold``), so other books/pages produce nothing.
"""

from __future__ import annotations

import re

# The box-name heading: a short all-caps run in Paizo's condensed display bold.
_HEAD_FONT = "GoodOT-CondBold"
_KIND_RE = re.compile(r"\b(SPELL|CANTRIP|FOCUS|RITUAL|FEAT)\s*(\d+)\b")
# Chapter-tab words printed vertically in the outer page margin — never box content.
_TAB_WORDS = frozenset({
    "Introduction", "Ancestries", "Backgrounds", "Classes", "Skills", "Feats",
    "Equipment", "Spells", "Game", "Playing", "Mastering", "Gamemastering",
    "Crafting", "Treasure", "Appendix", "Glossary", "Index", "Age", "Lost",
    "Omens", "Building", "Subsystems", "the", "of", "The", "&",
})


def _is_heading_span(s) -> bool:
    t = s["text"].strip().rstrip("\t").strip()
    return (11 <= s["size"] <= 13 and _HEAD_FONT in s["font"]
            and t.isupper() and len(t) >= 3
            and t.replace(" ", "").replace("-", "").replace("’", "").isalpha())


def _is_bold(s) -> bool:
    return bool(s["flags"] & 16) or "Bold" in s["font"] or "Semibold" in s["font"]


def _spans(page) -> list[dict]:
    # Drop the ``Gin`` display font: Paizo uses it only for page chrome — the
    # vertical chapter-tab margin ("The Age of Lost Omens", "Crafting & Treasure")
    # and running headers ("SPELLS" / "LEVEL") — which otherwise interleave into a
    # box's text. Headings, the level label, and body are GoodOT / Times, untouched.
    out = []
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            out.extend(s for s in line["spans"] if s["text"].strip() and "Gin" not in s["font"])
    return out


def _box_lines(spans: list[dict]) -> list[str]:
    """Render region spans as Markdown lines (one per visual line), wrapping short
    bold spans as ``**label**`` so field labels survive."""
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
            if not t or _KIND_RE.fullmatch(t.upper()):
                continue
            parts.append(f"**{t}**" if _is_bold(s) and len(t) <= 18 else t)
        text = re.sub(r"[ \t]+", " ", " ".join(parts)).strip()
        if text:
            out.append(text)
    return out


def page_spell_boxes(page) -> str:
    """Reconstructed ``##### **NAME KIND LEVEL**`` box Markdown for one page, or ""."""
    width = page.rect.width
    mid = width / 2
    spans = _spans(page)
    heads = [s for s in spans if _is_heading_span(s)]
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
                m = _KIND_RE.search(s["text"].upper())
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
                  and not _is_heading_span(s)
                  and s["text"].strip() not in _TAB_WORDS]
        body = "\n".join(_box_lines(region))
        boxes.append(f"##### **{name} {level}**\n{body}\n")
    return "\n".join(boxes)
