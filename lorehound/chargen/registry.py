"""Per-system chargen registry — the chargen counterpart of :mod:`lorehound.sources`.

Each system registers a :class:`SystemChargen`: how to match its game name, how to
snapshot the data its flow needs from the live rules index, and the flow generator
itself. The cog looks up ``chargen_for(game)`` and, if found, snapshots data and
starts a :class:`~lorehound.chargen.engine.ChargenSession`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .engine import FlowFactory


@dataclass
class SystemChargen:
    name: str
    games: tuple[str, ...]          # game-name substrings this matches (lowercased)
    build_flow: FlowFactory         # (ctx) -> flow generator
    # Snapshot the data the flow needs from RulesService, ONCE at session start, so an
    # in-flight character stays consistent even if the index is re-built mid-session.
    # Returns the object passed as ChargenContext.data. None → the flow needs no data.
    build_data: Callable[[object, str], object] | None = None

    def matches(self, game: str) -> bool:
        g = (game or "").lower()
        return any(k in g for k in self.games)


_REGISTRY: list[SystemChargen] = []


def register(system: SystemChargen) -> None:
    _REGISTRY.append(system)


def chargen_for(game: str) -> SystemChargen | None:
    """The first registered system whose game matches, else None (unsupported)."""
    for s in _REGISTRY:
        if s.matches(game):
            return s
    return None


def supported_games() -> list[str]:
    """Registered system display names (for help text / error messages)."""
    return [s.name for s in _REGISTRY]
