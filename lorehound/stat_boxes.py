"""Stat-box extraction — structure the boxed entries (spells, feats, focus
spells, rituals) that books lay out as a titled box with bold field labels.

Pathfinder 2e renders each as markdown like::

    ##### **FIREBALL SPELL 3**
    **Traditions** arcane, primal
    **Cast** somatic, verbal
    **Range** 500 feet; **Area** 20-foot burst
    **Saving Throw** basic Reflex
    A roaring blast of fire... **Heightened (+1)** The damage increases by 2d6.

so they parse straight from the extracted markdown — no PDF geometry needed. The
detector is keyed on that ``##### **NAME KIND LEVEL**`` heading plus ``**Field**``
labels, which is self-gating: a book without the pattern yields nothing. (The
KIND set is the only system-specific knob; lift it to a source profile later.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Box kinds we recognise in the heading (Pathfinder). The heading is
# ``NAME <KIND> <LEVEL>`` — e.g. "FIREBALL SPELL 3", "POWER ATTACK FEAT 1".
KINDS = ("SPELL", "CANTRIP", "FOCUS", "RITUAL", "FEAT")

_HEAD = re.compile(
    r"^#{3,6}\s*\*\*(?P<name>.+?)\s+(?P<kind>" + "|".join(KINDS) + r")\s+(?P<level>\d+)\*\*\s*$",
    re.M,
)
# A bold field label and its value (value runs to the next bold label or line end).
_FIELD = re.compile(r"\*\*([^*\n]+?)\*\*[ \t]*([^\n*]*)")
_PAGE = re.compile(r"\[\[page\s+(\d+)\]\]", re.I)
# Lines to drop from a box body before parsing: the per-page registration
# watermark, page markers, and the running header / date stamp that land between
# pages when a box spans a page break.
_NOISE_LINE = re.compile(
    r"(paizo\.co|wiggyjiggyjed|@g\s*ail|\[\[page|^\s*core\s+rulebook\s*$"
    r"|^[\s*]*(?:[a-z]{1,2}|\d{1,4})(?:[\s*]+(?:[a-z]{1,2}|\d{1,4}))*[\s*]*$)",
    re.I,
)
# A plausible field label: capitalised, a few words, no digits-only / watermark.
_FIELD_LABEL = re.compile(r"^[A-Z][A-Za-z][A-Za-z '()+/-]{0,24}$")


@dataclass
class StatBox:
    name: str
    kind: str                       # SPELL / FEAT / FOCUS / CANTRIP / RITUAL
    level: int
    fields: list[tuple[str, str]] = field(default_factory=list)  # (label, value), in order
    description: str = ""
    page: int | None = None

    @property
    def category(self) -> str:
        """Routing category — feats vs everything-spell-like."""
        return "feat" if self.kind == "FEAT" else "spell"


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", " ", s)).strip()


def _page_at(text: str, pos: int) -> int | None:
    """The page number of the nearest ``[[page N]]`` marker before ``pos``."""
    last = None
    for m in _PAGE.finditer(text, 0, pos):
        last = int(m.group(1))
    return last


def parse_stat_boxes(text: str) -> list[StatBox]:
    """Parse every ``##### **NAME KIND LEVEL**`` box out of extracted markdown."""
    heads = list(_HEAD.finditer(text))
    boxes: list[StatBox] = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else m.end() + 1200
        body = text[m.end():end]
        # drop watermark / page-marker / running-header noise lines
        lines = [ln for ln in body.split("\n") if ln.strip() and not _NOISE_LINE.search(ln)]
        clean_body = "\n".join(lines)

        fields: list[tuple[str, str]] = []
        for fm in _FIELD.finditer(clean_body):
            label, value = _clean(fm.group(1)), _clean(fm.group(2))
            if value and _FIELD_LABEL.match(label):
                fields.append((label, value))
        description = _clean(" ".join(ln for ln in lines if "**" not in ln))

        boxes.append(StatBox(
            name=_clean(m.group("name")),
            kind=m.group("kind"),
            level=int(m.group("level")),
            fields=fields,
            description=description,
            page=_page_at(text, m.start()),
        ))
    return boxes
