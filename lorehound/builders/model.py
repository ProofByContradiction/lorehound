"""The draft an equipment builder fills in — the builder counterpart of
:class:`~lorehound.chargen.model.CharacterDraft`.

The flow mutates it as choices are made; the render layer turns it into the built-item
card. The shared flow engine only ever touches ``log`` and ``complete``, so this stands
in for a CharacterDraft anywhere the engine expects a draft.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SuitBuild:
    """A powered-armour / Battle Dress build in progress: the chosen base suit and the
    options filling its slot budget. ``slots_used`` / ``options`` stay empty in the
    base-suit MVP and are the hook for the options fast-follow."""

    game: str
    base: str = ""            # suit family, e.g. "Battle Dress"
    grade: str = ""           # "Basic" | "Improved" | "Advanced" | ""
    protection: str = ""      # "+22" (may carry a vs-note)
    str_mod: str = ""         # suit STR modifier, e.g. "+4"
    dex_mod: str = ""         # suit DEX modifier
    tl: str = ""
    cost: str = ""            # base suit cost, e.g. "Cr200000"
    slots_total: int = 0      # slot capacity of the chosen suit
    slots_used: int = 0       # sum of installed option slot costs (options fast-follow)
    options: list[str] = field(default_factory=list)  # installed option labels
    source: str = ""          # book · page the catalogue came from
    log: list[str] = field(default_factory=list)      # narrative of the build
    complete: bool = False

    @property
    def slots_free(self) -> int:
        return max(0, self.slots_total - self.slots_used)

    @property
    def display(self) -> str:
        return f"{self.base} ({self.grade})" if self.grade else (self.base or "Powered Armour")


@dataclass
class ShipBuild:
    """A Traveller starship build in progress. The flow records the choices, then stores
    the computed per-component breakdown as primitives (label, tons, cost) so the render
    layer needs no ship-compute types."""

    game: str
    hull_tons: int = 0
    config: str = ""
    thrust: int = 0
    jump: int = 0
    power_plant: str = ""
    computer: str = ""
    sensor: str = ""
    staterooms: int = 0
    # computed breakdown, filled at the end of the flow
    lines: list[tuple[str, float, float]] = field(default_factory=list)  # (label, tons, MCr)
    tonnage_used: float = 0.0
    total_cost: float = 0.0
    source: str = ""
    warnings: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    complete: bool = False

    @property
    def tonnage_free(self) -> float:
        return max(0.0, self.hull_tons - self.tonnage_used)

    @property
    def display(self) -> str:
        return f"{self.hull_tons}t {self.config}".strip() if self.hull_tons else "Starship"


@dataclass
class RobotBuild:
    """A Traveller robot build: a chassis (slot budget) with a locomotion and slotted
    options. Costs are whole credits (robots are far cheaper than ships)."""

    game: str
    size: int = 0
    locomotion: str = ""
    base_cost: int = 0
    base_hits: int = 0
    slots_total: int = 0
    slots_used: int = 0
    options: list[str] = field(default_factory=list)
    total_cost: int = 0
    source: str = ""
    log: list[str] = field(default_factory=list)
    complete: bool = False

    @property
    def slots_free(self) -> int:
        return max(0, self.slots_total - self.slots_used)

    @property
    def display(self) -> str:
        return f"Size {self.size} {self.locomotion}".strip() if self.size else "Robot"
