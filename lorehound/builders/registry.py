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
    # Snapshot the component catalogue the flow needs from RulesService, ONCE at session
    # start, so an in-flight build stays consistent even if the index is rebuilt mid-flow.
    # Returns the object passed as the flow context's ``data``. None → needs no data.
    build_data: Callable[[object, str], object] | None = None

    def matches(self, game: str) -> bool:
        g = (game or "").lower()
        return any(k in g for k in self.games)


_REGISTRY: list[SystemBuilder] = []


def register(builder: SystemBuilder) -> None:
    _REGISTRY.append(builder)


def builder_for(game: str) -> SystemBuilder | None:
    """The first registered builder whose game matches, else None (unsupported)."""
    for b in _REGISTRY:
        if b.matches(game):
            return b
    return None


def supported_games() -> list[str]:
    """Registered builder display names (for help text / error messages)."""
    return [b.name for b in _REGISTRY]
