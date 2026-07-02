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


# --- installable slot options ----------------------------------------------------

# The genuine battle-dress slot options are the CSC tables with a real "Slots" column
# (Modification · TL · Effect · Slots · Cost — weapon mounts, anti-missile, armour
# modifications). These come from the markdown harvester (find_tables delabels them, and
# — worse — the 3-column tables it does keep put KG in the last column, which is weight,
# not slots). We only take Slots-headed, non-suit-catalogue tables, so a weight (kg)
# accessory is never mistaken for a slot cost.
_SLOT_MAX = 40


def _int_cell(cell: str) -> int | None:
    m = re.search(r"-?\d+", (cell or "").replace(",", ""))
    return int(m.group()) if m else None


def _armor_credits(cost: str) -> int:
    c = (cost or "").strip().replace(",", "")
    m = re.search(r"MCr([\d.]+)", c, re.I)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"Cr([\d.]+)", c, re.I)
    if m:
        return int(float(m.group(1)))
    return _int_cell(c) or 0


@dataclass(frozen=True)
class ArmorOption:
    """One installable slot option: its name, tech level, slot cost, and credit cost."""

    name: str
    tl: str
    slots: int
    cost: int = 0

    @property
    def key(self) -> str:
        return f"{self.name}|{self.tl}|{self.slots}"

    @property
    def label(self) -> str:
        unit = "slot" if self.slots == 1 else "slots"
        cr = f" · Cr{self.cost:,}" if self.cost else ""
        return f"{self.name} · {self.slots} {unit}{cr}"


def _col(header: list[str], *needles: str) -> int | None:
    ns = [n.lower() for n in needles]
    for i, h in enumerate(header):
        hl = (h or "").lower()
        if all(n in hl for n in ns):
            return i
    return None


def armor_options_from_tables(md_tables) -> list[ArmorOption]:
    """Collect battle-dress slot options from harvested markdown tables. A table qualifies
    when it has a real ``Slots`` column and is NOT a suit catalogue (no Armour Type /
    Protection column). Name is the first column; slot cost and price come from their
    named columns."""
    out: list[ArmorOption] = []
    seen: set[str] = set()
    for t in md_tables:
        rows = getattr(t, "rows", None) or []
        if len(rows) < 2:
            continue
        header = rows[0]
        si = _col(header, "slot")
        if si is None or _col(header, "armour type") is not None or _col(header, "protection") is not None:
            continue
        ci = _col(header, "cost")
        ti = _col(header, "tl")
        for r in rows[1:]:
            name = (r[0] or "").strip()
            slots = _int_cell(r[si]) if si < len(r) else None
            if not name or not any(c.isalpha() for c in name) or slots is None or slots > _SLOT_MAX:
                continue
            tl = str(_int_cell(r[ti]) or "") if ti is not None and ti < len(r) else ""
            cost = _armor_credits(r[ci]) if ci is not None and ci < len(r) else 0
            opt = ArmorOption(name=name, tl=tl, slots=slots, cost=cost)
            if opt.key not in seen:
                seen.add(opt.key)
                out.append(opt)
    return out


# --- the builder: catalogue snapshot + interactive flow --------------------------


@dataclass
class ArmorData:
    """The snapshot the flow drives: every buildable suit, grouped by family, plus a
    provenance string. Built once at session start from the live index."""

    game: str
    suits: list[ArmorSuit] = field(default_factory=list)
    options: list[ArmorOption] = field(default_factory=list)
    source: str = ""

    @property
    def families(self) -> list[str]:
        """Distinct family names in catalogue order (dedup preserving first-seen)."""
        return list(dict.fromkeys(s.name for s in self.suits))

    def grades_of(self, family: str) -> list[ArmorSuit]:
        return [s for s in self.suits if s.name == family]


def build_armor_data(rules, game: str) -> ArmorData:
    """Snapshot the powered-armour catalogue from the live index. Suits come from the
    STR/DEX/SLOTS master table among the game's chunks (grade-split at index time).
    Slot OPTIONS come from the harvested markdown tables that have a real ``Slots``
    column — the correct source (find_tables delabels them and puts kg, not slots, in the
    last column of the 3-column tables it does keep)."""
    from .. import pdf_tables, sources  # noqa: F401 — pdf_tables registers the profile

    prof = sources.profile_for(game)
    gs = prof.grade_split if prof else None
    suits: list[ArmorSuit] = []
    source = ""
    book = ""
    for c in getattr(rules.index, "chunks", []):
        if getattr(c, "game", None) != game:
            continue
        rows = getattr(c, "rows", None) or []
        cells = {(x or "").strip().upper() for r in rows for x in r}
        if {"STR", "DEX", "SLOTS"} <= cells and ("ARMOUR TYPE" in cells or "PROTECTION" in cells):
            parsed = suits_from_rows(rows, grade_split=gs)
            if parsed:
                suits = parsed
                book = getattr(c, "source", "")
                loc = getattr(c, "locator", "")
                source = f"{book}{' · ' + loc if loc else ''}".strip(" ·")
                break
    # Options come from the SAME book as the suits (the armour catalogue), not every
    # Traveller book's Slots-headed tables — else ship/robot grades ("Basic", "Small", …)
    # would flood the list.
    md_tables = [t for t in getattr(rules, "markdown_tables", {}).get(game, [])
                 if getattr(t, "source", "") == book]
    options = armor_options_from_tables(md_tables)
    options.sort(key=lambda o: (o.slots, o.name))
    return ArmorData(game=game, suits=suits, options=options, source=source)


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
    if data.options:
        yield from _options_loop(ctx, data.options)


_DONE = "__done__"


def _options_loop(ctx, options: list[ArmorOption]):
    """Add options one at a time until the user finishes or the slot budget is full. Each
    add is its own step, so the engine's Back removes the last option (replay). An option
    already installed — or another variant of the same-named one — is dropped from the
    pick-list (you don't fit the same upgrade twice)."""
    draft = ctx.draft
    i = 0
    while True:
        free = draft.slots_free
        installed = set(draft.options)
        addable = [o for o in options if o.slots <= free and o.name not in installed]
        if not addable:
            draft.log.append("No more options fit." if free else "Slot budget full.")
            return
        shown = addable[:24]  # leave a slot for Finish within the 25-option Select cap
        detail = f"{free} of {draft.slots_total} slots free"
        if len(addable) > len(shown):
            detail += f" · showing the {len(shown)} cheapest of {len(addable)} that fit"
        pick = yield Step(
            id=f"option-{i}",
            kind=StepKind.CHOICE,
            essential=True,
            prompt="Add an option, or finish",
            detail=detail,
            options=[Option(value=_DONE, label="✓ Finish — build as-is")]
            + [Option(value=o.key, label=o.label[:100]) for o in shown],
        )
        if pick.value == _DONE:
            return
        chosen = next((o for o in addable if o.key == pick.value), None)
        if chosen is None:
            return
        draft.options.append(chosen.name)
        draft.slots_used += chosen.slots
        draft.log.append(f"+ {chosen.name} ({chosen.slots} slots)")
        i += 1


def _register() -> None:
    from .model import SuitBuild
    from .registry import SystemBuilder, register
    from .render import build_summary, built_suit_sheet
    register(SystemBuilder(
        name="Traveller — powered armour / Battle Dress",
        games=("traveller",),
        kind="armour",
        noun="powered-armour suit",
        emoji="🛡️",
        build_flow=armor_flow,
        build_data=build_armor_data,
        render_sheet=built_suit_sheet,
        render_summary=build_summary,
        make_draft=lambda game: SuitBuild(game=game),
    ))


_register()
