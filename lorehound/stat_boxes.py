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
labels, which is self-gating: a book without the pattern yields nothing. The KIND set
is no longer hardcoded — categories beyond the known spell-family/feats are derived
from the document when they recur (see :func:`_accepted_kinds`).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .text_utils import repair_ligatures

# Box kinds we always recognise in the heading (Pathfinder's spell-family + feats).
# The heading is ``NAME <KIND> <LEVEL>`` — e.g. "FIREBALL SPELL 3", "POWER ATTACK FEAT 1".
KINDS = ("SPELL", "CANTRIP", "FOCUS", "RITUAL", "FEAT")
_SPELL_KINDS = frozenset({"SPELL", "CANTRIP", "FOCUS", "RITUAL"})

# Generic box heading: a ``NAME <CAPWORD> <LEVEL>`` where the category is any ≥3-letter
# all-caps word. This lets us pick up box categories beyond the hardcoded set (e.g.
# Pathfinder's ITEM / HAZARD / RUNE / SNARE boxes, currently dropped) without naming
# them — but a *novel* category must recur across the book (``_MIN_KIND_RECURRENCE``)
# so a stray prose heading never becomes a spurious entry. Known kinds always qualify,
# so existing spell/feat cards are unchanged.
_HEAD_ANY = re.compile(
    r"^#{3,6}\s*\*\*(?P<name>.+?)\s+(?P<kind>[A-Z][A-Z’'\-]{2,})\s+(?P<level>\d+)\*\*\s*$",
    re.M,
)
_MIN_KIND_RECURRENCE = 3

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
# A plausible field label: capitalised, a few words. Must START with two letters
# (so watermark tokens like "18"/"2023" are rejected), but digits are allowed after
# that so "Heightened (+1)" / "Heightened (3rd)" count as fields, not lost text.
_FIELD_LABEL = re.compile(r"^[A-Z][A-Za-z][A-Za-z0-9 '()+/-]{0,24}$")

# A line that opens a "Heightened (Nth)" entry. Its value is prose that can wrap onto
# a new *sentence* (capital-initial) — e.g. Teleport's "...same solar system. Assuming
# you have accurate knowledge...". The lowercase-only wrap rule below would leave that
# sentence orphaned into the description, so a Heightened entry keeps absorbing plain
# lines (any case) until the next bold label. Scoped to Heightened specifically so the
# interleaved bleed in PF's multi-column feat layout (capital "Reflexive Shield 6"
# fragments) is untouched.
_HEIGHTENED_HEAD = re.compile(r"^\*\*Heightened\b", re.I)


@dataclass
class StatBox:
    name: str
    kind: str                       # SPELL / FEAT / FOCUS / CANTRIP / RITUAL
    level: int | None               # None = unknown (a recovered Type-2 feat with no level)
    fields: list[tuple[str, str]] = field(default_factory=list)  # (label, value), in order
    description: str = ""
    page: int | None = None

    @property
    def category(self) -> str:
        """Routing category: feats, the spell family, or a box category of its own
        (item / hazard / rune / snare / …) so a non-spell box isn't mislabelled as a
        spell. Only ``spell`` and ``feat`` become dedicated cards; the rest are
        searchable chunks."""
        if self.kind == "FEAT":
            return "feat"
        if self.kind in _SPELL_KINDS:
            return "spell"
        return self.kind.lower()


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", " ", s)).strip()


def _page_at(text: str, pos: int) -> int | None:
    """The page number of the nearest ``[[page N]]`` marker before ``pos``."""
    last = None
    for m in _PAGE.finditer(text, 0, pos):
        last = int(m.group(1))
    return last


def _accepted_kinds(heads: list[re.Match]) -> set[str]:
    """The box categories to keep: the known kinds (always) plus any novel capword
    category that recurs at least ``_MIN_KIND_RECURRENCE`` times across the document —
    the recurrence gate keeps a one-off ``##### **X FOO 1**`` in prose from becoming a
    bogus entry while admitting a real, repeated category the book uses."""
    counts = Counter(m.group("kind") for m in heads)
    accepted = {k for k in counts if k in KINDS}
    accepted |= {k for k, n in counts.items() if n >= _MIN_KIND_RECURRENCE}
    return accepted


# ── Feat "sidebar bleed" ──────────────────────────────────────────────────────
# Pathfinder's two-column feat pages carry an alphabetical "feats by level" index
# column that bleeds into the extracted prose: <Feat Name> <Level> fragments spliced
# mid-sentence (POWER ATTACK's "...multiple Savage Critical 18 Shatter Defenses 6
# attack penalty..."). They're invisible junk on a feat card. The tell that separates
# them from a real "Add 5 feet" is that the fragments form a MONOTONIC ALPHABETICAL
# run — so we strip only the members of a long increasing run, never a lone incidental
# match. Corpus-measured: a clean feat has ≤2 such candidates and a bled one has 5–30,
# with a clean gap between, so a run of ≥4 is a trigger that touches no clean feat.
_BLEED_WORD = r"[A-Z][a-z]+(?:['’\-][A-Za-z]+)?"
_BLEED_FRAG = re.compile(
    rf"\b({_BLEED_WORD}(?:[ ](?:of|the|and|to|{_BLEED_WORD})){{0,4}})[ ](\d{{1,2}})\b"
)
_BLEED_MIN_RUN = 4
# Capitalised prose-starters that can glue onto the FRONT of a bled fragment name
# ("...Make a Strike. The Incredible Ricochet 12" → the name is "Incredible Ricochet").
_BLEED_LEAD = frozenset(
    "The You Your This That These Those A An If When While And But Make It Its Each "
    "Both He She They We Add As Also Then All Any Use".split()
)
_BLEED_CONN = frozenset({"of", "the", "and", "to"})  # lowercase connectors inside a name


def _bleed_candidates(desc: str) -> list[tuple[int, int, str]]:
    """(start, end, name) for each ``<Name> <1-20>`` sidebar-fragment candidate, with a
    leading prose-starter or trailing connector trimmed off the name."""
    out: list[tuple[int, int, str]] = []
    for m in _BLEED_FRAG.finditer(desc):
        if not 1 <= int(m.group(2)) <= 20:
            continue
        words = m.group(1).split(" ")
        start = m.start(1)
        while len(words) > 1 and words[0] in _BLEED_LEAD:
            start += len(words[0]) + 1
            words = words[1:]
        while len(words) > 1 and words[-1] in _BLEED_CONN:
            words = words[:-1]
        if len(words) == 1 and words[0] in _BLEED_LEAD:
            continue
        out.append((start, m.end(), " ".join(words)))
    return out


def _longest_alpha_run(names: list[str]) -> list[int]:
    """Indices of a longest non-decreasing (case-insensitive) subsequence of ``names``
    — the alphabetical sidebar run threaded through the prose."""
    keys = [n.lower() for n in names]
    n = len(keys)
    if not n:
        return []
    best = [1] * n
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if keys[j] <= keys[i] and best[j] + 1 > best[i]:
                best[i], prev[i] = best[j] + 1, j
    end = max(range(n), key=lambda i: best[i])
    run: list[int] = []
    while end != -1:
        run.append(end)
        end = prev[end]
    return run[::-1]


def _strip_feat_bleed(desc: str) -> str:
    """Remove the alphabetical feats-by-level sidebar fragments bled into a feat's
    description. Iterates: a sidebar that spans columns restarts alphabetically, so each
    pass strips one run until none of length ``_BLEED_MIN_RUN`` remains."""
    for _ in range(8):
        cands = _bleed_candidates(desc)
        if len(cands) < _BLEED_MIN_RUN:
            break
        run = _longest_alpha_run([c[2] for c in cands])
        if len(run) < _BLEED_MIN_RUN:
            break
        spans = sorted((cands[i][0], cands[i][1]) for i in run)
        kept, last = [], 0
        for s, e in spans:
            kept.append(desc[last:s])
            last = e
        kept.append(desc[last:])
        new = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s+", " ", " ".join(kept)).strip())
        if new == desc:
            break
        desc = new
    return desc


# ── Type-2 feat headings ──────────────────────────────────────────────────────
# Some PF feats (most ancestry feats, plus a per-class "additional feats" column) lost
# their ``##### **NAME FEAT N**`` markup in extraction, leaving the name as a bare bold
# line ``**NAME**`` directly above a class/ancestry trait line — so `_HEAD_ANY` never
# matches and the feat is missing as a card entirely. Recover them: the trait line is
# the anchor (a bold class/ancestry tag right below the name), which keeps a stray bold
# phrase from being mistaken for a feat. Level survives only where the ancestry pages
# group feats under a ``#### NTH LEVEL`` header; class feats carry no such header, so
# their level is left unknown rather than guessed.
_T2_NAME = re.compile(r"^\*\*([A-Z][A-Z0-9 ,'’\-]{2,44})\*\*$")
_T2_TAG = re.compile(r"\*\*([A-Z][A-Z’'\-]{1,})\*\*")
_T2_CLASS_TRAITS = frozenset(
    "BARBARIAN BARD CHAMPION CLERIC DRUID FIGHTER MONK RANGER ROGUE SORCERER WIZARD "
    "ALCHEMIST DWARF ELF GNOME GOBLIN HALFLING HUMAN".split()
)
# Bold ALL-CAPS lines that are traits/labels, not feat names — never start a box.
_T2_NOT_A_FEAT = _T2_CLASS_TRAITS | frozenset(
    "RAGE STANCE PRESS FLOURISH CONCENTRATE OPEN METAMAGIC ATTACK SKILL GENERAL ARCHETYPE "
    "MORPH PRIMAL OCCULT DIVINE ARCANE TRANSMUTATION POLYMORPH AUDITORY VISUAL EMOTION "
    "MENTAL DEATH INCAPACITATION MANIPULATE MOVE FORTUNE MISFORTUNE RARE UNCOMMON COMMON".split()
)
# NOTE: a recovered Type-2 feat's LEVEL is deliberately left unknown. The pages do carry
# ``#### NTH LEVEL`` group headers, but two-column extraction scrambles the linear order
# so the nearest header does NOT reliably govern the feat below it — e.g. "CAVE CLIMBER"
# follows a "13TH LEVEL" header yet sits amid boxed FEAT 1 / FEAT 5 entries (it's level 5).
# Rather than emit a wrong level we emit none; the card simply omits the Level row.


@dataclass
class _Heading:
    start: int          # char offset where the heading begins (for page + bounding)
    body: int           # char offset where the box body begins (just after the heading)
    name: str
    kind: str
    level: int | None


def _type2_feat_headings(text: str, known: set[str]) -> list[_Heading]:
    """Recover Type-2 feat headings: a bold ``**NAME**`` line whose next non-blank line
    carries a bold class/ancestry trait. Skips names already parsed as a box or that are
    themselves traits/labels."""
    lines = text.split("\n")
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    out: list[_Heading] = []
    for i, ln in enumerate(lines):
        m = _T2_NAME.match(ln.strip())
        if not m:
            continue
        name = _clean(m.group(1))
        up = name.upper()
        if up in known or up in _T2_NOT_A_FEAT:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines) or not set(_T2_TAG.findall(lines[j])) & _T2_CLASS_TRAITS:
            continue  # the following line must carry a class/ancestry trait — the anchor
        known.add(up)  # de-dupe a repeated bold name
        out.append(_Heading(starts[i], starts[i] + len(ln), name, "FEAT", None))  # level unknown
    return out


def _collect_headings(text: str) -> list[_Heading]:
    """All box headings in document order: the ``##### **NAME KIND LEVEL**`` boxes plus
    the recovered Type-2 feat headings, sorted so each box body is bounded by the next
    heading of either kind."""
    matches = list(_HEAD_ANY.finditer(text))
    accepted = _accepted_kinds(matches)
    heads = [
        _Heading(m.start(), m.end(), _clean(m.group("name")), m.group("kind"), int(m.group("level")))
        for m in matches
        if m.group("kind") in accepted
    ]
    heads.extend(_type2_feat_headings(text, {h.name.upper() for h in heads}))
    heads.sort(key=lambda h: h.start)
    return heads


def parse_stat_boxes(text: str) -> list[StatBox]:
    """Parse every ``##### **NAME KIND LEVEL**`` box out of extracted markdown, plus the
    recovered Type-2 feat headings (see :func:`_type2_feat_headings`). The box category
    is derived from the headings (see :func:`_accepted_kinds`) rather than a fixed list,
    so categories the book uses beyond spells/feats are recovered too."""
    heads = _collect_headings(text)
    boxes: list[StatBox] = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start if i + 1 < len(heads) else h.body + 1200
        body = text[h.body:end]
        # drop watermark / page-marker / running-header noise lines
        raw = [ln.strip() for ln in body.split("\n") if ln.strip() and not _NOISE_LINE.search(ln)]
        # Merge a wrapped continuation — a non-label line starting lowercase — into
        # the previous line, so a field value that wraps to the next visual line keeps
        # its tail ("...1 undead" + "creature") instead of leaking it into the
        # description. The description's own first line starts with a capital, so it
        # isn't absorbed into the last field.
        lines: list[str] = []
        for ln in raw:
            cont = lines and not ln.startswith("**")
            wrap = cont and (ln[:1].islower() or _HEIGHTENED_HEAD.match(lines[-1]))
            if wrap:
                lines[-1] = f"{lines[-1]} {ln}"
            else:
                lines.append(ln)
        clean_body = "\n".join(lines)

        fields: list[tuple[str, str]] = []
        seen_labels: set[str] = set()
        for fm in _FIELD.finditer(clean_body):
            # trailing ';'/',' is the separator before the next inline **Field** ("500
            # feet; **Area** ...") — drop it so the value reads "500 feet".
            label, value = _clean(fm.group(1)), _clean(fm.group(2)).rstrip(" ;,")
            # Keep the first value per label: PF lays feats out in interleaved
            # columns, so a box can pick up a neighbour's repeated **Prerequisites**.
            if value and _FIELD_LABEL.match(label) and label not in seen_labels:
                fields.append((label, repair_ligatures(value)))
                seen_labels.add(label)
        description = repair_ligatures(_clean(" ".join(ln for ln in lines if "**" not in ln)))
        if h.kind == "FEAT":  # strip PF's interleaved feats-by-level sidebar
            description = _strip_feat_bleed(description)

        boxes.append(StatBox(
            name=h.name,
            kind=h.kind,
            level=h.level,
            fields=fields,
            description=description,
            page=_page_at(text, h.start),
        ))
    return boxes
