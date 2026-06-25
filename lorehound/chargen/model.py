"""System-agnostic data structures for character generation.

A per-system flow (a generator) yields :class:`Step` objects and is sent back a
:class:`StepResult` for each. The accumulating character lives in
:class:`CharacterDraft`, which the flow mutates as it goes so the UI can render
work-in-progress. None of this is system-specific — Twilight 2000, Traveller, and
any future system share these types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StepKind(Enum):
    """What a step asks for, which decides how the engine resolves it.

    * ``INFO``   — show text, then continue (no input).
    * ``ROLL``   — roll dice; auto-rolled in quick mode, surfaced in faithful mode.
    * ``CHOICE`` — pick one option; pauses in faithful mode, and in quick mode only
      when the step is ``essential`` (a genuine decision) — otherwise auto-picked.
    """

    INFO = "info"
    ROLL = "roll"
    CHOICE = "choice"


@dataclass(frozen=True)
class Option:
    """One selectable option in a ``CHOICE`` step."""

    value: str            # stable id sent back to the flow
    label: str            # shown to the user
    description: str = ""  # optional sub-label (e.g. a requirement)


@dataclass
class Step:
    """One thing the flow wants resolved before it can continue."""

    id: str
    kind: StepKind
    prompt: str
    options: list[Option] = field(default_factory=list)  # CHOICE only
    roll_spec: str = ""                                   # ROLL only, e.g. "2d6"
    # In quick mode a CHOICE pauses only when essential; rolls/info never pause.
    essential: bool = False
    detail: str = ""                                      # extra context to display


@dataclass
class StepResult:
    """The resolved answer for a step, sent back into the flow generator.

    ``value`` is the chosen option's value (CHOICE) or a short label (ROLL/INFO);
    ``total`` is the rolled sum for a ROLL; ``detail`` is a human-readable trace
    (e.g. a dice breakdown or the chosen label) used for the run log.
    """

    step_id: str
    value: str = ""
    total: int | None = None
    detail: str = ""


@dataclass
class CharacterDraft:
    """The character being built — filled in by the flow, rendered by the UI."""

    game: str
    method: str = ""                                          # e.g. "lifepath"
    name: str = ""
    attributes: dict[str, str] = field(default_factory=dict)  # name -> rating/value
    skills: dict[str, str] = field(default_factory=dict)      # name -> rating/level
    specialties: list[str] = field(default_factory=list)
    career_history: list[str] = field(default_factory=list)   # per-term summaries
    rank: str = ""
    gear: list[str] = field(default_factory=list)
    derived: dict[str, str] = field(default_factory=dict)     # Hit/Stress Capacity…
    notes: dict[str, str] = field(default_factory=dict)       # nationality, rads, buddy…
    log: list[str] = field(default_factory=list)              # narrative of the build
    complete: bool = False
