"""System-agnostic careers / classes for the ``/class`` command.

A **career** (T2K career, Traveller career, a class in another system) is modelled
generically as a name plus an ordered list of *sections* — each section is either a
labelled value ("Requirements: STR B+") or a small grid (a "Specialities (D6)" roll
table). The command, autocomplete, and renderer all work against this one model.

How a game's careers are discovered is pluggable, via two strategies:

* **Structured detectors** run at index time over the recovered tables. Each handles
  one *content shape*, keyed on structural signals (not a hardcoded career list), and
  emits clean :class:`Career` objects. ``detect_careers`` currently ships the T2K
  **column-card** detector (each card column is a career).
* **Search-assemble** is the generic fallback for systems whose career data isn't
  cleanly structured (e.g. Mongoose Traveller, whose career tables are fragmented):
  given a name, it retrieves the game's career-related chunks and assembles a card on
  the fly. Lower fidelity, but works for any system with zero per-system extraction.

Add a system by writing a structured detector (preferred) or relying on assemble.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_D6_MARKER = re.compile(r"(?:special|talent|perk).*\(?\s*d6", re.I)  # "SPECIALITIES (D6)"
_ROLL_LABEL = re.compile(r"^\d{1,2}$")          # a roll-table row index: "1".."12"
_NAME_OK = re.compile(r"[A-Za-z]{2,}")          # a real name has letters


@dataclass
class CareerSection:
    """One labelled part of a career card — a value *or* a small grid, not both."""
    label: str
    text: str = ""
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Career:
    game: str
    name: str
    source: str
    locator: str
    sections: list[CareerSection] = field(default_factory=list)
    assembled: bool = False  # True = search-assembled (lower-fidelity), False = structured


def _looks_like_name(cell: str) -> bool:
    """A career-name header cell: has letters, isn't a roll index or a D6 marker."""
    c = cell.strip()
    if not c or _ROLL_LABEL.match(c) or _D6_MARKER.search(c):
        return False
    return bool(_NAME_OK.search(c))


def _title(s: str) -> str:
    """Normalize an ALL-CAPS card label/name for display ("REQUIREMENTS" ->
    "Requirements"), but keep short all-caps acronyms intact ("EMT" stays "EMT")."""
    if not s.strip():
        return s.strip()
    out = []
    for w in s.split():
        if w.isalpha() and w.isupper() and len(w) <= 3:
            out.append(w)              # acronym: EMT, FBI
        elif w.isupper():
            out.append(w.title())      # FIREMAN -> Fireman, ARMS -> Arms
        else:
            out.append(w)              # already mixed-case
    return " ".join(out)


# --- Structured detector: column cards (T2K) --------------------------------


def _is_specialty_grid(rows) -> bool:
    """A standalone SPECIALTY (D6) roll grid: an *unnamed* header (a D6 marker, or
    no career names) over rows whose first cell is a roll index. These sit on their
    own page beside the named card whose careers they belong to (T2K military)."""
    rows = [[(c or "").strip() for c in r] for r in rows]
    if len(rows) < 3:
        return False
    h = rows[0]
    unnamed = bool(_D6_MARKER.search(h[0])) or not any(_looks_like_name(x) for x in h)
    numbered = sum(1 for r in rows[1:] if r and _ROLL_LABEL.match(r[0]))
    return unnamed and numbered >= 2


def _specialty_column(grid_rows, col: int) -> list[list[str]]:
    """Pull career column ``col`` out of a specialty grid as a ``Roll (D6)`` table."""
    rows = [[(c or "").strip() for c in r] for r in grid_rows]
    out = [["Roll (D6)", "Specialty"]]
    for r in rows[1:]:
        if r and _ROLL_LABEL.match(r[0]) and col < len(r) and r[col]:
            out.append([r[0], r[col]])
    return out if len(out) > 1 else []


def careers_from_card(chunk, sibling_specialties=()) -> list[Career]:
    """Parse a T2K-style *column card* into one :class:`Career` per career column.

    Layout (rows): ``row[0] = [<label>, NAME1, NAME2, ...]`` then field rows
    ``[FIELD, val1, val2, ...]``; a ``SPECIALITIES (D6)`` field begins a numbered
    sub-table whose following ``1..6`` rows give each career's per-roll options.
    The first cell of ``row[0]`` is a label (CAREER / blank); a *numeric* first cell
    means this is a stray roll-table fragment, not a career header — skip it.

    ``sibling_specialties`` are specialty grids (same column layout) found on a
    neighbouring page — used to supply specialties for cards (the T2K *military*
    careers) whose own specialty table was split onto the next page.
    """
    rows = [[(c or "").strip() for c in r] for r in (chunk.rows or [])]
    if len(rows) < 2:
        return []
    header = rows[0]
    if not header or _ROLL_LABEL.match(header[0]) or _D6_MARKER.search(header[0]):
        return []  # a roll-table fragment mis-tagged as a card, not a career header
    if header[0].strip().upper().startswith("LAST CAREER"):
        return []  # the draft mechanic — its columns are categories, not careers
    name_cols = [i for i in range(1, len(header)) if _looks_like_name(header[i])]
    if not name_cols:
        return []

    careers: list[Career] = []
    for i in name_cols:
        sections: list[CareerSection] = []
        cur: CareerSection | None = None   # the section continuation rows attach to
        spec_rows: list[list[str]] = []
        spec_label = "Specialities (D6)"
        in_spec = False
        for r in rows[1:]:
            label = r[0].strip() if r else ""
            val = r[i].strip() if i < len(r) else ""
            if _D6_MARKER.search(label):
                in_spec, cur = True, None
                spec_label = _title(label)
                spec_rows = [["Roll (D6)", "Specialty"]]
                continue
            if in_spec and _ROLL_LABEL.match(label):
                if val:
                    spec_rows.append([label, val])
                continue
            in_spec = False
            if label:
                # A labelled row always opens a new section, even when this column's
                # value is blank — so its continuation rows attach here, not above.
                cur = CareerSection(label=_title(label), text=val)
                sections.append(cur)
            elif val and cur is not None:
                # A blank-label row is a continuation of the current field (e.g. a
                # multi-row gear list) — fold it in.
                cur.text = f"{cur.text}, {val}" if cur.text else val
            elif val:
                cur = CareerSection(label="", text=val)
                sections.append(cur)
        if len(spec_rows) > 1:
            sections.append(CareerSection(label=spec_label, rows=spec_rows))
        # If this card carried no specialties of its own, graft them from a sibling
        # specialty grid on the next page (column index lines up — both are
        # [label, career1, career2, ...]).
        if not any(s.rows for s in sections):
            for sg in sibling_specialties:
                col = _specialty_column(sg, i)
                if col:
                    sections.append(CareerSection(label="Specialities (D6)", rows=col))
                    break
        sections = [s for s in sections if s.text or s.rows]  # drop empty fields
        if sections:
            careers.append(
                Career(
                    game=chunk.game,
                    name=_title(header[i]),
                    source=chunk.source,
                    locator=chunk.locator,
                    sections=sections,
                )
            )
    return careers


def detect_careers(chunks) -> dict[str, dict[str, Career]]:
    """Build the structured-career index: ``game -> {name_lower -> Career}``.

    Runs the structured detectors over the indexed chunks. Cards are grouped by
    source so a card whose specialty table was split onto a neighbouring page (the
    T2K military careers) can borrow it from a sibling specialty grid of matching
    width. Games absent here fall back to :func:`assemble_career` at query time.
    """
    from collections import defaultdict

    by_source: dict[tuple, list] = defaultdict(list)
    for c in chunks:
        if c.category == "card" and getattr(c, "rows", None):
            by_source[(c.game, c.source)].append(c)

    index: dict[str, dict[str, Career]] = {}
    for cards in by_source.values():
        spec_grids = [c for c in cards if _is_specialty_grid(c.rows)]
        for c in cards:
            width = max((len(r) for r in c.rows), default=0)
            # Sibling specialty grids that share this card's column layout (same
            # width) — their columns line up with this card's career columns.
            sibs = [
                g.rows
                for g in spec_grids
                if g is not c and max((len(r) for r in g.rows), default=0) == width
            ]
            for career in careers_from_card(c, sibling_specialties=sibs):
                index.setdefault(career.game, {})[career.name.lower()] = career
    return index


# --- Generic fallback: search-assemble --------------------------------------

# Words that signal a chunk carries career structure (qualification, progression,
# skills, ranks, mustering-out) — used to prefer career chunks when assembling.
_CAREER_SIGNAL = re.compile(
    r"\b(qualif|surviv|advance|muster|rank|assignment|specialist|"
    r"service skill|personal development|career|term)\b",
    re.I,
)


def assemble_career(search, game: str, name: str, *, top_k: int = 12) -> Career | None:
    """Generic fallback: assemble a career card from retrieved chunks.

    ``search`` is a ``RulesService.search``-compatible callable. We pull the game's
    chunks most relevant to ``name``, prefer those with career structure, and present
    them as sections (tables kept as grids). Returns ``None`` if nothing matches.
    """
    hits = search(f"{name} career qualification skills rank", game=game, top_k=top_k)
    if not hits:
        return None
    nl = name.lower()

    # Only assemble from chunks that actually mention the career — generic
    # qualification/skills prose that names no career is misleading noise.
    named = [h for h in hits if nl in f"{h.chunk.section} {h.chunk.text[:500]}".lower()]
    if not named:
        return None
    named.sort(
        key=lambda h: (bool(_CAREER_SIGNAL.search(f"{h.chunk.section} {h.chunk.text[:400]}")), h.score),
        reverse=True,
    )
    hits = named
    sections: list[CareerSection] = []
    seen: set[str] = set()
    for h in hits:
        c = h.chunk
        leaf = (c.section.split("›")[-1].strip() if c.section else "") or c.category.title()
        label = f"{leaf} · {c.locator}".strip(" ·")
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        if c.rows:
            sections.append(CareerSection(label=label, rows=c.rows))
        else:
            sections.append(CareerSection(label=label, text=" ".join(c.text.split())[:600]))
        if len(sections) >= 5:
            break
    if not sections:
        return None
    top = hits[0].chunk
    return Career(
        game=game,
        name=name,
        source=top.source,
        locator=top.locator,
        sections=sections,
        assembled=True,
    )
