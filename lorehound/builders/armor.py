"""Traveller powered-armour / Battle Dress builder — the DATA layer.

The Central Supply Catalogue's master powered-armour table (CSC p40) lists every
Battle Dress variant with a *per-suit slot budget*: a builder picks a base suit and
fills those slots with options. This module turns that catalogue table — as it comes
out of the index — into clean, typed :class:`ArmorSuit` records a flow can drive.

The table crams a family's Basic/Improved/Advanced grades into one cell-row
(``Battle Dress, Basic Battle Dress, Improved …`` over ``+22 +25 +28`` / ``16 16 18``),
so we reuse the Traveller ``SourceProfile``'s :class:`~lorehound.sources.GradeSplit` to
explode them. GradeSplit is deliberately conservative: it will not split a protection
cell that carries a note (``+22 (+32 vs. fire…) +25 (…) +28 (…)``), leaving that value
merged across all three grade rows. We finish the job here by *grade-indexing* such a
merged cell — assigning each family row the protection group at its own position.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..chargen.model import Option, Step, StepKind
from .registry import SystemBuilder, register

# Columns pulled out of the master armour table, matched by header NAME (order in the
# printed table varies, so never rely on a fixed index). SLOTS is the slot budget.
_WANT_COLS = ("PROTECTION", "TL", "STR", "DEX", "SLOTS", "COST")

# One protection value, optionally trailing a parenthetical note: "+22",
# "+22 (+32 vs. fire, lasers, and energy)", "+26 (+ ½ PSI)". The note is consumed as a
# whole ``(...)`` group so its inner "+" doesn't start a spurious split. Used to break a
# merged multi-grade protection cell back into its per-grade groups.
_PROT_GROUP = re.compile(r"[+±]\s*(?:\d+|½)(?:\s*\([^)]*\))?")

_LEADING_INT = re.compile(r"-?\d+")


def _col_index(header: list[str], name: str) -> int | None:
    for j, h in enumerate(header):
        if (h or "").strip().upper() == name:
            return j
    return None


def _slots_int(cell: str) -> int:
    m = _LEADING_INT.search(cell or "")
    return int(m.group()) if m else 0


def _prot_groups(cell: str) -> list[str]:
    """The distinct protection values in a cell — one for a clean ``+22``, several for
    a merged multi-grade ``+22 (…) +25 (…) +28 (…)``."""
    return [g.strip() for g in _PROT_GROUP.findall(cell or "")]


@dataclass(frozen=True)
class ArmorSuit:
    """One buildable base suit: a single grade of a Battle Dress family, with the slot
    budget a builder fills. ``str_mod`` / ``dex_mod`` are the suit's characteristic
    modifiers; ``protection`` may carry a vs-note (e.g. ``+22 (+32 vs. fire…)``)."""

    name: str          # family, e.g. "Battle Dress"
    grade: str         # "Basic" | "Improved" | "Advanced" | ""  (a clean single-suit)
    protection: str    # "+22" (grade-indexed; may carry a note)
    tl: str            # "13"
    str_mod: str       # "+4"
    dex_mod: str       # "+4"
    slots: int         # slot capacity, e.g. 16
    cost: str          # "Cr200000" / "MCr1.2"

    @property
    def display(self) -> str:
        return f"{self.name} ({self.grade})" if self.grade else self.name


def _base_and_grade(full_name: str) -> tuple[str, str]:
    """``"Battle Dress, Advanced"`` -> ``("Battle Dress", "Advanced")``; a name with no
    grade suffix returns an empty grade."""
    if "," in full_name:
        base, grade = full_name.split(",", 1)
        return base.strip(), grade.strip()
    return full_name.strip(), ""


def suits_from_rows(rows: list[list[str]], *, grade_split=None) -> list[ArmorSuit]:
    """Parse the master powered-armour table into per-grade :class:`ArmorSuit` records.

    ``grade_split`` is the Traveller profile's :class:`~lorehound.sources.GradeSplit`
    (pass ``sources.profile_for(game).grade_split``); when given, the crammed grade rows
    are exploded first. Columns are located by header name, so a differently-ordered
    printing still parses. Protection cells that GradeSplit left merged (note-carrying
    suits) are grade-indexed here so each grade gets its own value."""
    if not rows or len(rows) < 2:
        return []
    work = [list(r) for r in rows]
    if grade_split is not None:
        work = grade_split.apply(work)
    header = [(c or "").strip().upper() for c in work[0]]
    idx = {name: _col_index(header, name) for name in _WANT_COLS}
    if idx["PROTECTION"] is None or idx["SLOTS"] is None:
        return []  # not the powered-armour table

    def cell(row: list[str], name: str) -> str:
        j = idx[name]
        return (row[j] if j is not None and j < len(row) else "") or ""

    # Walk data rows, grouping consecutive rows that share a base family name. Within a
    # family whose protection came out merged, hand each row the group at its position.
    suits: list[ArmorSuit] = []
    data = [r for r in work[1:] if r and (r[0] or "").strip()]
    i = 0
    while i < len(data):
        base, _ = _base_and_grade(data[i][0])
        fam = [data[i]]
        j = i + 1
        while j < len(data) and _base_and_grade(data[j][0])[0] == base:
            fam.append(data[j])
            j += 1
        # Grade-index a merged protection cell only when its group count matches the
        # family size; otherwise trust the per-row value GradeSplit already produced.
        groups = _prot_groups(cell(fam[0], "PROTECTION"))
        use_grouped = len(groups) == len(fam) and len(fam) > 1
        for k, row in enumerate(fam):
            _, grade = _base_and_grade(row[0])
            prot = groups[k] if use_grouped else cell(row, "PROTECTION").strip()
            suits.append(
                ArmorSuit(
                    name=base,
                    grade=grade,
                    protection=prot,
                    tl=cell(row, "TL").strip(),
                    str_mod=cell(row, "STR").strip(),
                    dex_mod=cell(row, "DEX").strip(),
                    slots=_slots_int(cell(row, "SLOTS")),
                    cost=cell(row, "COST").strip(),
                )
            )
        i = j
    return suits


# --- the builder: catalogue snapshot + interactive flow --------------------------


@dataclass
class ArmorData:
    """The snapshot the flow drives: every buildable suit, grouped by family, plus a
    provenance string. Built once at session start from the live index."""

    game: str
    suits: list[ArmorSuit] = field(default_factory=list)
    source: str = ""

    @property
    def families(self) -> list[str]:
        """Distinct family names in catalogue order (dedup preserving first-seen)."""
        return list(dict.fromkeys(s.name for s in self.suits))

    def grades_of(self, family: str) -> list[ArmorSuit]:
        return [s for s in self.suits if s.name == family]


def build_armor_data(rules, game: str) -> ArmorData:
    """Snapshot the powered-armour catalogue from the live index. Finds the one table
    carrying the STR/DEX/SLOTS powered-armour columns (the CSC master table) among the
    game's chunks, and parses it into suits. The chunk's rows are already grade-split at
    index time (``profile.normalize_rows``), so re-applying the grade-split here is a
    harmless no-op that also covers a hypothetical un-normalised source."""
    from .. import pdf_tables, sources  # noqa: F401 — pdf_tables registers the profile

    prof = sources.profile_for(game)
    gs = prof.grade_split if prof else None
    for c in getattr(rules.index, "chunks", []):
        if getattr(c, "game", None) != game:
            continue
        rows = getattr(c, "rows", None) or []
        cells = {(x or "").strip().upper() for r in rows for x in r}
        if {"STR", "DEX", "SLOTS"} <= cells and ("ARMOUR TYPE" in cells or "PROTECTION" in cells):
            suits = suits_from_rows(rows, grade_split=gs)
            if suits:
                loc = getattr(c, "locator", "")
                src = f"{getattr(c, 'source', '')}{' · ' + loc if loc else ''}".strip(" ·")
                return ArmorData(game=game, suits=suits, source=src)
    return ArmorData(game=game, suits=[])


def _short_protection(protection: str) -> str:
    """Drop a trailing vs-note for a compact pick-list label (the full value, note and
    all, still lands on the finished card)."""
    return protection.split(" (", 1)[0].strip()


def _apply_suit(draft, suit: ArmorSuit, source: str) -> None:
    draft.base = suit.name
    draft.grade = suit.grade
    draft.protection = suit.protection
    draft.str_mod = suit.str_mod
    draft.dex_mod = suit.dex_mod
    draft.tl = suit.tl
    draft.cost = suit.cost
    draft.slots_total = suit.slots
    draft.source = source
    draft.log.append(f"Base suit: {suit.display} — {suit.slots} slots")


def armor_flow(ctx):
    """The base-suit MVP flow: choose a family, then a grade; the finished draft carries
    the suit's stats and its slot budget. (Slot-consuming options are the fast-follow.)"""
    data: ArmorData | None = ctx.data
    draft = ctx.draft
    if data is None or not data.suits:
        draft.log.append("No powered-armour catalogue is indexed for this game.")
        return

    pick = yield Step(
        id="family",
        kind=StepKind.CHOICE,
        essential=True,
        prompt="Choose a base suit",
        detail="Each Battle Dress family trades protection, characteristics and slot capacity.",
        options=[Option(value=f, label=f) for f in data.families[:25]],
    )
    family = pick.value
    draft.base = family  # so the grade step already shows the family taking shape
    grades = data.grades_of(family)

    if len(grades) > 1:
        gpick = yield Step(
            id="grade",
            kind=StepKind.CHOICE,
            essential=True,
            prompt=f"Choose a grade of {family}",
            options=[
                Option(
                    value=s.grade or family,
                    label=f"{s.grade or 'Standard'} · PROT {_short_protection(s.protection)} "
                          f"· {s.slots} slots · {s.cost}",
                )
                for s in grades
            ],
        )
        suit = next((s for s in grades if (s.grade or family) == gpick.value), grades[0])
    else:
        suit = grades[0]

    _apply_suit(draft, suit, data.source)


register(SystemBuilder(
    name="Traveller — powered armour / Battle Dress",
    games=("traveller",),
    build_flow=armor_flow,
    build_data=build_armor_data,
))
