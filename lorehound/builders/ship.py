"""Traveller starship builder (High Guard) — DATA + COMPUTE layer.

The construction tables come from the markdown harvester (:mod:`lorehound.markdown_tables`),
which recovers them *labelled* where ``find_tables`` delabels them. This module turns
those tables into a typed component catalogue and does the Core-MVP construction maths —
hull, M-drive, J-drive, power plant, bridge, computer, sensors — with a tonnage budget
and cost. Every constant is grounded in the Mongoose 2022 High Guard rules:

* Hull: Cr50,000 per ton, modified by configuration (Streamlined +20% cost, …).
* Manoeuvre drive: Thrust% of hull tonnage; MCr2 per ton; Power = 10% × hull × Thrust.
* Jump drive: (Jump% of hull) + 5 tons, minimum 10 tons; MCr1.5 per ton;
  Power = 10% × hull × Jump.
* Power plant: sized to meet basic systems (20% of hull) + drive Power, at the plant
  type's Power-per-Ton; cost at its Cost-per-Ton.
* Bridge: tonnage by ship size (Bridges table); cost MCr0.5 per 100 tons of hull.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# --- grounded construction constants (Mongoose 2022 High Guard) -------------------
HULL_COST_PER_TON = 50_000          # Cr, a basic hull
M_DRIVE_MCR_PER_TON = 2.0
J_DRIVE_MCR_PER_TON = 1.5
J_DRIVE_EXTRA_TONS = 5
J_DRIVE_MIN_TONS = 10
BASIC_POWER_FRACTION = 0.20         # of hull tonnage
DRIVE_POWER_FRACTION = 0.10         # × hull × rating, for each of M- and J-drive
BRIDGE_MCR_PER_100T = 0.5


def _mcr(cost: str) -> float:
    """A cost cell → millions of credits. ``MCr0.4`` → 0.4, ``Cr30000`` → 0.03,
    ``—``/blank → 0.0."""
    c = (cost or "").strip().replace(",", "")
    if not c or c in ("—", "-"):
        return 0.0
    m = re.search(r"MCr([\d.]+)", c, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"Cr([\d.]+)", c, re.I)
    if m:
        return float(m.group(1)) / 1_000_000
    m = re.search(r"[\d.]+", c)
    return float(m.group()) / 1_000_000 if m else 0.0


def _pct(cell: str) -> float:
    m = re.search(r"([\d.]+)\s*%", cell or "")
    return float(m.group(1)) / 100 if m else 0.0


def _int(cell: str) -> int | None:
    m = re.search(r"-?\d+", (cell or "").replace(",", ""))
    return int(m.group()) if m else None


# --- typed catalogue -------------------------------------------------------------

@dataclass(frozen=True)
class HullConfig:
    name: str
    cost_modifier: float   # +0.20 for Streamlined, -0.10, …


@dataclass(frozen=True)
class DriveStep:
    rating: int
    percent_hull: float
    tl: int | None


@dataclass(frozen=True)
class PowerPlant:
    name: str
    power_per_ton: float
    cost_per_ton: float    # MCr


@dataclass(frozen=True)
class Computer:
    name: str
    tl: int | None
    cost: float            # MCr


@dataclass(frozen=True)
class Sensor:
    name: str
    tl: int | None
    tons: float
    power: int
    cost: float            # MCr


@dataclass
class ShipData:
    game: str
    source: str = ""
    configs: list[HullConfig] = field(default_factory=list)
    thrust: dict[int, DriveStep] = field(default_factory=dict)
    jump: dict[int, DriveStep] = field(default_factory=dict)
    power_plants: list[PowerPlant] = field(default_factory=list)
    bridges: list[tuple[int, int]] = field(default_factory=list)  # (max_ship_tons, bridge_tons)
    computers: list[Computer] = field(default_factory=list)
    sensors: list[Sensor] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Enough of the catalogue parsed to build a ship."""
        return bool(self.configs and self.thrust and self.jump and self.power_plants)

    def config(self, name: str) -> HullConfig | None:
        return next((c for c in self.configs if c.name == name), None)

    def power_plant(self, name: str) -> PowerPlant | None:
        return next((p for p in self.power_plants if p.name == name), None)

    def bridge_tons(self, hull_tons: int) -> int:
        for max_tons, btons in sorted(self.bridges):
            if hull_tons <= max_tons:
                return btons
        return self.bridges[-1][1] if self.bridges else 0


# --- parsers (harvested MarkdownTable.rows → catalogue) ---------------------------

def _find(tables, *needles):
    """The first harvested table whose title/header contains all needles (upper)."""
    ns = [n.upper() for n in needles]
    for t in tables:
        blob = (t.title + " " + " ".join(t.header)).upper()
        if all(n in blob for n in ns):
            return t
    return None


def parse_configs(t) -> list[HullConfig]:
    if not t:
        return []
    hdr = [h.upper() for h in t.rows[0]]
    ccol = next((i for i, h in enumerate(hdr) if "HULL COST" in h or h == "COST"), len(hdr) - 1)
    out = []
    for r in t.rows[1:]:
        name = (r[0] or "").strip()
        if not name:
            continue
        cell = r[ccol] if ccol < len(r) else ""
        m = re.search(r"([+-]?\d+)\s*%", cell or "")
        out.append(HullConfig(name=name, cost_modifier=(int(m.group(1)) / 100) if m else 0.0))
    return out


def _parse_drive_potential(tables_for_page) -> dict[int, DriveStep]:
    """Thrust/Jump Potential tables are transposed: row 0 = ratings, a ``% of Hull`` row,
    a ``TL`` row (ratings run across the columns). Merge every matching sub-table."""
    steps: dict[int, DriveStep] = {}
    for t in tables_for_page:
        rows = t.rows
        rating_row = rows[0]
        pct_row = next((r for r in rows if r and "% of hull" in r[0].lower()), None)
        tl_row = next((r for r in rows if r and r[0].strip().upper().endswith("TL")), None)
        if not pct_row:
            continue
        for i in range(1, len(rating_row)):
            rating = _int(rating_row[i])
            if rating is None:
                continue
            pct = _pct(pct_row[i]) if i < len(pct_row) else 0.0
            if pct <= 0:
                continue
            tl = _int(tl_row[i]) if tl_row and i < len(tl_row) else None
            steps[rating] = DriveStep(rating=rating, percent_hull=pct, tl=tl)
    return steps


def parse_thrust(tables) -> dict[int, DriveStep]:
    return _parse_drive_potential([t for t in tables if "thrust potential" in t.title.lower()])


def parse_jump(tables) -> dict[int, DriveStep]:
    return _parse_drive_potential([t for t in tables if "jump potential" in t.title.lower()])


def parse_power_plants(t) -> list[PowerPlant]:
    if not t:
        return []
    out = []
    for r in t.rows[1:]:
        name = (r[0] or "").strip()
        ppt = float(_int(r[1]) or 0) if len(r) > 1 else 0.0
        if name and ppt > 0:
            out.append(PowerPlant(name=name, power_per_ton=ppt, cost_per_ton=_mcr(r[2] if len(r) > 2 else "")))
    return out


_TONS_BRACKET = re.compile(r"(\d[\d,]*)")


def parse_bridges(t) -> list[tuple[int, int]]:
    if not t:
        return []
    out = []
    for r in t.rows[1:]:
        label, size = (r[0] or ""), (r[1] if len(r) > 1 else "")
        btons = _int(size)
        nums = [int(n.replace(",", "")) for n in _TONS_BRACKET.findall(label)]
        if btons is None or not nums:
            continue
        max_tons = nums[-1] if ("or less" in label.lower() or "–" in label or "-" in label) else nums[0]
        out.append((max_tons, btons))
    return out


def parse_computers(t) -> list[Computer]:
    if not t:
        return []
    out = []
    for r in t.rows[1:]:
        name = (r[0] or "").strip()
        if name and "/" in name:
            out.append(Computer(name=name, tl=_int(r[1]) if len(r) > 1 else None,
                                 cost=_mcr(r[2] if len(r) > 2 else "")))
    return out


def parse_sensors(t) -> list[Sensor]:
    if not t:
        return []
    hdr = [h.upper() for h in t.rows[0]]

    def col(name):
        return next((i for i, h in enumerate(hdr) if h == name), None)

    ci_tl, ci_pow, ci_tons, ci_cost = col("TL"), col("POWER"), col("TONS"), col("COST")
    out = []
    for r in t.rows[1:]:
        name = (r[0] or "").strip()
        if not name:
            continue
        out.append(Sensor(
            name=name,
            tl=_int(r[ci_tl]) if ci_tl is not None and ci_tl < len(r) else None,
            tons=float(_int(r[ci_tons]) or 0) if ci_tons is not None and ci_tons < len(r) else 0.0,
            power=_int(r[ci_pow]) or 0 if ci_pow is not None and ci_pow < len(r) else 0,
            cost=_mcr(r[ci_cost]) if ci_cost is not None and ci_cost < len(r) else 0.0,
        ))
    return out


def ship_data_from_tables(tables, game: str, source: str = "") -> ShipData:
    """Build the catalogue from a document's harvested markdown tables."""
    return ShipData(
        game=game,
        source=source,
        configs=parse_configs(_find(tables, "HULL CONFIGURATION") or _find(tables, "CONFIGURATION")),
        thrust=parse_thrust(tables),
        jump=parse_jump(tables),
        power_plants=parse_power_plants(_find(tables, "POWER PLANT TYPE") or _find(tables, "POWER PER TON")),
        bridges=parse_bridges(_find(tables, "SIZE OF BRIDGE")),
        computers=parse_computers(_find(tables, "COMPUTERS PROCESSING") or _find(tables, "PROCESSING")),
        sensors=parse_sensors(_find(tables, "SENSORS")),
    )


# --- compute ---------------------------------------------------------------------

@dataclass
class Line:
    label: str
    tons: float
    cost: float   # MCr


@dataclass
class ShipReport:
    hull_tons: int
    lines: list[Line]
    warnings: list[str] = field(default_factory=list)

    @property
    def tonnage_used(self) -> float:
        return sum(line.tons for line in self.lines)

    @property
    def tonnage_free(self) -> float:
        return max(0.0, self.hull_tons - self.tonnage_used)

    @property
    def total_cost(self) -> float:
        return sum(line.cost for line in self.lines)


def m_drive_tons(hull: int, step: DriveStep) -> float:
    return round(hull * step.percent_hull, 1)


def j_drive_tons(hull: int, step: DriveStep) -> float:
    return max(J_DRIVE_MIN_TONS, round(hull * step.percent_hull, 1) + J_DRIVE_EXTRA_TONS)


def power_required(hull: int, thrust: int, jump: int) -> float:
    return (BASIC_POWER_FRACTION * hull
            + DRIVE_POWER_FRACTION * hull * thrust
            + DRIVE_POWER_FRACTION * hull * jump)


def compute_ship(data: ShipData, *, hull_tons: int, config: str, thrust: int, jump: int,
                 power_plant: str, computer: str = "", sensor: str = "") -> ShipReport:
    """The Core-MVP construction maths → a per-component tonnage/cost breakdown."""
    lines: list[Line] = []
    warnings: list[str] = []

    cfg = data.config(config)
    cost_mod = cfg.cost_modifier if cfg else 0.0
    hull_cost = hull_tons * HULL_COST_PER_TON / 1_000_000 * (1 + cost_mod)
    lines.append(Line(f"Hull — {hull_tons}t {config}".rstrip(), 0.0, hull_cost))

    ts = data.thrust.get(thrust)
    if ts:
        mt = m_drive_tons(hull_tons, ts)
        lines.append(Line(f"M-Drive (Thrust {thrust})", mt, mt * M_DRIVE_MCR_PER_TON))
    js = data.jump.get(jump)
    if js:
        jt = j_drive_tons(hull_tons, js)
        lines.append(Line(f"J-Drive (Jump {jump})", jt, jt * J_DRIVE_MCR_PER_TON))

    pp = data.power_plant(power_plant)
    need = power_required(hull_tons, thrust, jump)
    if pp:
        pt = math.ceil(need / pp.power_per_ton)
        lines.append(Line(f"Power Plant — {pp.name}", float(pt), pt * pp.cost_per_ton))
    else:
        warnings.append("no power plant selected")

    bt = data.bridge_tons(hull_tons)
    lines.append(Line("Bridge", float(bt), BRIDGE_MCR_PER_100T * hull_tons / 100))

    if computer:
        comp = next((c for c in data.computers if c.name == computer), None)
        if comp:
            lines.append(Line(f"Computer — {comp.name}", 0.0, comp.cost))
    if sensor:
        sen = next((s for s in data.sensors if s.name == sensor), None)
        if sen:
            lines.append(Line(f"Sensors — {sen.name}", sen.tons, sen.cost))

    report = ShipReport(hull_tons=hull_tons, lines=lines, warnings=warnings)
    if report.tonnage_used > hull_tons:
        warnings.append(f"over tonnage: {report.tonnage_used:.0f}t used of {hull_tons}t")
    return report


# --- catalogue snapshot + interactive flow ---------------------------------------

def build_ship_data(rules, game: str) -> ShipData:
    """Snapshot the ship construction catalogue from the harvested markdown tables the
    index built for this game (see RulesService.markdown_tables)."""
    tables = getattr(rules, "markdown_tables", {}).get(game, [])
    source = ""
    for t in tables:
        if "thrust potential" in t.title.lower() or "hull configuration" in t.title.lower():
            source = getattr(t, "source", "")
            break
    return ship_data_from_tables(tables, game, source)


# Standard hull tonnages to offer (High Guard allows any size; these cover the common
# adventure-class range without an open-ended prompt).
_HULL_SIZES = [100, 200, 300, 400, 600, 800, 1000, 2000]
_NONE = "__none__"


def _mod_label(m: float) -> str:
    return "" if not m else f" ({'+' if m > 0 else ''}{int(m * 100)}% cost)"


def ship_flow(ctx):
    """Core-MVP ship flow: hull tonnage → configuration → Thrust → Jump → power plant →
    computer → sensors, then compute the tonnage/cost breakdown onto the draft."""
    from ..chargen.model import Option, Step, StepKind

    data: ShipData | None = ctx.data
    draft = ctx.draft
    if data is None or not data.ok:
        draft.log.append("No ship construction catalogue is indexed for this game.")
        return

    def choose(step_id, prompt, options, detail=""):
        return (yield Step(id=step_id, kind=StepKind.CHOICE, essential=True,
                           prompt=prompt, detail=detail, options=options[:25]))

    pick = yield from choose("hull", "Choose a hull tonnage",
                             [Option(str(s), f"{s} tons") for s in _HULL_SIZES])
    draft.hull_tons = int(pick.value)

    pick = yield from choose("config", "Hull configuration",
                             [Option(c.name, f"{c.name}{_mod_label(c.cost_modifier)}") for c in data.configs])
    draft.config = pick.value

    tratings = sorted(r for r in data.thrust if 1 <= r <= 9)
    pick = yield from choose("thrust", "Manoeuvre drive (Thrust)", [
        Option(str(r), f"Thrust {r} · {data.thrust[r].percent_hull * 100:g}% of hull · TL{data.thrust[r].tl}")
        for r in tratings])
    draft.thrust = int(pick.value)

    jratings = sorted(r for r in data.jump if 1 <= r <= 9)
    pick = yield from choose("jump", "Jump drive (Jump)", [
        Option(str(r), f"Jump {r} · {data.jump[r].percent_hull * 100:g}% of hull · TL{data.jump[r].tl}")
        for r in jratings])
    draft.jump = int(pick.value)

    pick = yield from choose("power", "Power plant", [
        Option(p.name, f"{p.name} · {p.power_per_ton:g} Power/ton · MCr{p.cost_per_ton:g}/ton")
        for p in data.power_plants],
        detail=f"needs {power_required(draft.hull_tons, draft.thrust, draft.jump):.0f} Power")
    draft.power_plant = pick.value

    if data.computers:
        pick = yield from choose("computer", "Computer (optional)",
                                 [Option(_NONE, "— none —")]
                                 + [Option(c.name, f"{c.name} · TL{c.tl} · MCr{c.cost:g}") for c in data.computers])
        draft.computer = "" if pick.value == _NONE else pick.value

    if data.sensors:
        pick = yield from choose("sensor", "Sensors (optional)",
                                 [Option(_NONE, "— none —")]
                                 + [Option(s.name, f"{s.name} · TL{s.tl} · {s.tons:g}t · MCr{s.cost:g}")
                                    for s in data.sensors])
        draft.sensor = "" if pick.value == _NONE else pick.value

    report = compute_ship(data, hull_tons=draft.hull_tons, config=draft.config,
                          thrust=draft.thrust, jump=draft.jump, power_plant=draft.power_plant,
                          computer=draft.computer, sensor=draft.sensor)
    draft.lines = [(line.label, line.tons, line.cost) for line in report.lines]
    draft.tonnage_used = report.tonnage_used
    draft.total_cost = report.total_cost
    draft.warnings = report.warnings
    draft.source = data.source
    draft.log.append(f"Built {draft.hull_tons}t {draft.config} ship, {draft.tonnage_used:.0f}t used")


def _register() -> None:
    from .model import ShipBuild
    from .registry import SystemBuilder, register
    from .render import built_ship_sheet, ship_summary
    register(SystemBuilder(
        name="Traveller — starship (High Guard)",
        games=("traveller",),
        kind="ship",
        noun="starship",
        emoji="🚀",
        build_flow=ship_flow,
        build_data=build_ship_data,
        render_sheet=built_ship_sheet,
        render_summary=ship_summary,
        make_draft=lambda game: ShipBuild(game=game),
    ))


_register()
