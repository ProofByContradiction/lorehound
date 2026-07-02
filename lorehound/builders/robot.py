"""Traveller robot builder (Robot Handbook) — DATA + COMPUTE + flow.

Like the ship builder, this is only possible because the markdown harvester
(:mod:`lorehound.markdown_tables`) recovers the construction tables *labelled* where
``find_tables`` reduces the Robot Handbook to a blank design worksheet. A robot is a
**chassis** (a size giving base slots / hits / cost) with a **locomotion** (a cost
multiplier) whose slots are filled with **options** — the same slot-budget shape as the
powered-armour builder.

Core-MVP: chassis size → locomotion → slot options, computing the slot budget and cost
(base cost × locomotion multiplier, plus each option's cost). Brains / skills / weapons
are a fast-follow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def _credits(cost: str) -> int:
    """A cost cell → whole credits. ``Cr1000`` → 1000, ``MCr1.2`` → 1200000,
    ``2000`` → 2000, ``—``/blank → 0."""
    c = (cost or "").strip().replace(",", "")
    if not c or c in ("—", "-"):
        return 0
    m = re.search(r"MCr([\d.]+)", c, re.I)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"Cr([\d.]+)", c, re.I)
    if m:
        return int(float(m.group(1)))
    m = re.search(r"[\d.]+", c)
    return int(float(m.group())) if m else 0


def _int(cell: str) -> int | None:
    m = re.search(r"-?\d+", (cell or "").replace(",", ""))
    return int(m.group()) if m else None


@dataclass(frozen=True)
class Chassis:
    size: int
    base_slots: int
    base_hits: int
    cost: int            # credits
    equivalent: str = ""  # "Human, Vargr, Dolphin" — a size hint for the picker

    @property
    def label(self) -> str:
        eq = f" ({self.equivalent.split(',')[0].strip()}-sized)" if self.equivalent else ""
        return f"Size {self.size}{eq}"


@dataclass(frozen=True)
class Locomotion:
    name: str
    cost_multiplier: float
    agility: str = ""
    tl: int | None = None


@dataclass(frozen=True)
class RobotOption:
    name: str
    slots: int
    cost: int            # credits

    @property
    def key(self) -> str:
        return f"{self.name}|{self.slots}|{self.cost}"

    @property
    def label(self) -> str:
        unit = "slot" if self.slots == 1 else "slots"
        cr = f" · Cr{self.cost:,}" if self.cost else ""
        return f"{self.name} · {self.slots} {unit}{cr}"


# --- parsers (harvested MarkdownTable → catalogue) -------------------------------

def _col(header: list[str], *needles: str) -> int | None:
    """Index of the first header cell containing all needles (case-insensitive) — robust
    to pymupdf's doubled labels ('Size Base Slots' still matches 'base slots')."""
    ns = [n.lower() for n in needles]
    for i, h in enumerate(header):
        hl = (h or "").lower()
        if all(n in hl for n in ns):
            return i
    return None


def parse_chassis(t) -> list[Chassis]:
    if not t:
        return []
    hdr = t.rows[0]
    ci_slots = _col(hdr, "base", "slots")
    ci_hits = _col(hdr, "base", "hits")
    ci_cost = _col(hdr, "cost")
    ci_eq = _col(hdr, "equivalent", "size")
    out = []
    for r in t.rows[1:]:
        size = _int(r[0]) if r else None
        slots = _int(r[ci_slots]) if ci_slots is not None and ci_slots < len(r) else None
        if size is None or slots is None:
            continue
        out.append(Chassis(
            size=size, base_slots=slots,
            base_hits=_int(r[ci_hits]) or 0 if ci_hits is not None and ci_hits < len(r) else 0,
            cost=_credits(r[ci_cost]) if ci_cost is not None and ci_cost < len(r) else 0,
            equivalent=(r[ci_eq].strip() if ci_eq is not None and ci_eq < len(r) else ""),
        ))
    return out


def parse_locomotion(t) -> list[Locomotion]:
    if not t:
        return []
    hdr = t.rows[0]
    ci_mult = _col(hdr, "cost", "multiplier")
    ci_ag = _col(hdr, "agility")
    ci_tl = _col(hdr, "tl")
    out = []
    for r in t.rows[1:]:
        name = (r[0] or "").strip()
        mult_cell = r[ci_mult] if ci_mult is not None and ci_mult < len(r) else ""
        m = re.search(r"x\s*([\d.]+)", mult_cell or "", re.I)
        if not name or not m:
            continue
        out.append(Locomotion(
            name=name, cost_multiplier=float(m.group(1)),
            agility=(r[ci_ag].strip() if ci_ag is not None and ci_ag < len(r) else ""),
            tl=_int(r[ci_tl]) if ci_tl is not None and ci_tl < len(r) else None,
        ))
    return out


def parse_options(tables) -> list[RobotOption]:
    """Every named Item/Slots/Cost option row across the robot option tables, deduped."""
    seen: set[str] = set()
    out: list[RobotOption] = []
    for t in tables:
        hdr = [h.lower() for h in t.rows[0]]
        if "slots" not in hdr or "item" not in hdr or "cost" not in hdr:
            continue
        ci_slots, ci_cost = hdr.index("slots"), len(hdr) - 1 - hdr[::-1].index("cost")
        for r in t.rows[1:]:
            name = re.sub(r"<br\s*/?>", " ", (r[0] or "")).strip()
            # Skip blanks, junk ("(spare)"), and same-named repeats (an item listed at
            # two TLs) — keep the first, so the pick-list has one entry per option.
            if len(name) < 3 or name.startswith("(") or not any(c.isalpha() for c in name):
                continue
            slots = _int(r[ci_slots]) if ci_slots < len(r) else None
            if slots is None or name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append(RobotOption(name=name, slots=slots, cost=_credits(r[ci_cost]) if ci_cost < len(r) else 0))
    return out


@dataclass
class RobotData:
    game: str
    source: str = ""
    chassis: list[Chassis] = field(default_factory=list)
    locomotions: list[Locomotion] = field(default_factory=list)
    options: list[RobotOption] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.chassis and self.locomotions)

    def chassis_of(self, size: int) -> Chassis | None:
        return next((c for c in self.chassis if c.size == size), None)

    def locomotion(self, name: str) -> Locomotion | None:
        return next((loco for loco in self.locomotions if loco.name == name), None)


def robot_data_from_tables(tables, game: str, source: str = "") -> RobotData:
    def find(*needles):
        ns = [n.lower() for n in needles]
        best = None
        for t in tables:
            blob = (t.title + " " + " ".join(t.rows[0])).lower()
            if all(n in blob for n in ns) and len([r for r in t.rows[1:] if r and r[0].strip()]) >= 2:
                best = t
        return best

    # The chassis Size table: header has "Base Slots" and "Basic Cost", rows keyed by size.
    chassis_t = next(
        (t for t in tables
         if _col(t.rows[0], "base", "slots") is not None and _col(t.rows[0], "cost") is not None
         and any((r[0] or "").strip().isdigit() for r in t.rows[1:])),
        None,
    )
    loco_t = next((t for t in tables if _col(t.rows[0], "cost", "multiplier") is not None), None)
    return RobotData(
        game=game, source=source,
        chassis=parse_chassis(chassis_t),
        locomotions=parse_locomotion(loco_t),
        options=parse_options(tables),
    )


# --- compute ---------------------------------------------------------------------

@dataclass
class RobotReport:
    chassis: Chassis
    locomotion: Locomotion
    options: list[RobotOption]

    @property
    def base_cost(self) -> int:
        return round(self.chassis.cost * self.locomotion.cost_multiplier)

    @property
    def options_cost(self) -> int:
        return sum(o.cost for o in self.options)

    @property
    def total_cost(self) -> int:
        return self.base_cost + self.options_cost

    @property
    def slots_used(self) -> int:
        return sum(o.slots for o in self.options)

    @property
    def slots_free(self) -> int:
        return max(0, self.chassis.base_slots - self.slots_used)


# --- catalogue snapshot + flow ---------------------------------------------------

def build_robot_data(rules, game: str) -> RobotData:
    """Snapshot the robot construction catalogue from the harvested markdown tables."""
    all_tables = getattr(rules, "markdown_tables", {}).get(game, [])
    source = ""
    for t in all_tables:
        if t.rows and _col(t.rows[0], "cost", "multiplier") is not None:
            source = getattr(t, "source", "")
            break
    # Scope to the Robot Handbook so other books' Item/Slots/Cost tables don't leak in.
    tables = [t for t in all_tables if getattr(t, "source", "") == source] if source else all_tables
    return robot_data_from_tables(tables, game, source)


_DONE = "__done__"


def robot_flow(ctx):
    """Core-MVP robot flow: chassis size → locomotion → slot options."""
    from ..chargen.model import Option, Step, StepKind

    data: RobotData | None = ctx.data
    draft = ctx.draft
    if data is None or not data.ok:
        draft.log.append("No robot construction catalogue is indexed for this game.")
        return

    pick = yield Step(
        id="chassis", kind=StepKind.CHOICE, essential=True,
        prompt="Choose a chassis size",
        detail="Bigger chassis = more slots and hits, but higher cost.",
        options=[Option(str(c.size), f"{c.label} · {c.base_slots} slots · {c.base_hits} hits · Cr{c.cost:,}")
                 for c in sorted(data.chassis, key=lambda c: c.size)[:25]],
    )
    draft.size = int(pick.value)
    chassis = data.chassis_of(draft.size)
    draft.slots_total = chassis.base_slots
    draft.base_hits = chassis.base_hits

    pick = yield Step(
        id="locomotion", kind=StepKind.CHOICE, essential=True,
        prompt="Choose a locomotion",
        options=[Option(loco.name, f"{loco.name} · ×{loco.cost_multiplier:g} cost"
                        + (f" · Agility {loco.agility}" if loco.agility not in ("", "—") else ""))
                 for loco in data.locomotions[:25]],
    )
    draft.locomotion = pick.value
    loco = data.locomotion(draft.locomotion)
    draft.base_cost = round(chassis.cost * loco.cost_multiplier)

    chosen: list[RobotOption] = []
    i = 0
    while data.options:
        free = chassis.base_slots - sum(o.slots for o in chosen)
        addable = [o for o in data.options if o.slots <= free]
        if not addable:
            break
        shown = addable[:24]
        pick = yield Step(
            id=f"option-{i}", kind=StepKind.CHOICE, essential=True,
            prompt="Add an option, or finish",
            detail=f"{free} of {chassis.base_slots} slots free",
            options=[Option(_DONE, "✓ Finish — build as-is")]
            + [Option(o.key, o.label[:100]) for o in shown],
        )
        if pick.value == _DONE:
            break
        opt = next((o for o in addable if o.key == pick.value), None)
        if opt is None:
            break
        chosen.append(opt)
        draft.options.append(opt.name)
        draft.slots_used += opt.slots
        draft.log.append(f"+ {opt.name} ({opt.slots} slots)")
        i += 1

    report = RobotReport(chassis=chassis, locomotion=loco, options=chosen)
    draft.total_cost = report.total_cost
    draft.source = data.source
    draft.log.append(f"Built a Size {draft.size} {draft.locomotion} robot")


def _register() -> None:
    from .model import RobotBuild
    from .registry import SystemBuilder, register
    from .render import built_robot_sheet, robot_summary
    register(SystemBuilder(
        name="Traveller — robot (Robot Handbook)",
        games=("traveller",),
        kind="robot",
        noun="robot",
        emoji="🤖",
        build_flow=robot_flow,
        build_data=build_robot_data,
        render_sheet=built_robot_sheet,
        render_summary=robot_summary,
        make_draft=lambda game: RobotBuild(game=game),
    ))


_register()
