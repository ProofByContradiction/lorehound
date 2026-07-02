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
