"""Per-system builder registry — the builder counterpart of
:mod:`lorehound.chargen.registry`.

Each buildable system registers a :class:`SystemBuilder`: how to match its game name,
how to snapshot the component catalogue its flow needs from the live rules index, and
the flow generator itself. The cog looks up ``builder_for(game)`` and, if found,
snapshots data and starts a flow session.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..chargen.engine import FlowFactory


@dataclass
class SystemBuilder:
    name: str
    games: tuple[str, ...]          # game-name substrings this matches (lowercased)
    build_flow: FlowFactory         # (ctx) -> flow generator
    # A game can have several buildables (armour, ship, …); ``kind`` is the short
    # selector the ``/build type:`` option offers, and ``noun`` names the thing built.
    kind: str = ""
    noun: str = "item"
    emoji: str = "🛠️"
    # Snapshot the component catalogue the flow needs from RulesService, ONCE at session
    # start, so an in-flight build stays consistent even if the index is rebuilt mid-flow.
    # Returns the object passed as the flow context's ``data``. None → needs no data.
    build_data: Callable[[object, str], object] | None = None
    # Render the finished build's card and the running step summary from the draft — each
    # buildable has its own (a suit vs. a ship), so the cog stays generic.
    render_sheet: Callable[[object], str] | None = None
    render_summary: Callable[[object], str] | None = None
    # Make a fresh draft for the flow (given the game name). Each buildable has its own
    # draft type (SuitBuild, ShipBuild); the engine only needs it to have log/complete.
    make_draft: Callable[[str], object] | None = None

    def matches(self, game: str) -> bool:
        g = (game or "").lower()
        return any(k in g for k in self.games)


_REGISTRY: list[SystemBuilder] = []


def register(builder: SystemBuilder) -> None:
    _REGISTRY.append(builder)


def builders_for(game: str) -> list[SystemBuilder]:
    """Every registered builder whose game matches (a game can offer several)."""
    return [b for b in _REGISTRY if b.matches(game)]


def builder_for(game: str, kind: str | None = None) -> SystemBuilder | None:
    """The matching builder for ``game`` — the one whose ``kind`` matches when given,
    else the sole/first one. None if the game has no builder (or no such kind)."""
    matches = builders_for(game)
    if kind:
        return next((b for b in matches if b.kind == kind), None)
    return matches[0] if matches else None


def supported_games() -> list[str]:
    """Registered builder display names (for help text / error messages)."""
    return [b.name for b in _REGISTRY]
