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
from dataclasses import dataclass

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
