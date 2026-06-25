"""The system-agnostic chargen engine.

A per-system flow is a generator: it ``yield``s :class:`Step`s and is ``.send()``
the resolved :class:`StepResult` for each, which lets it express loops and branches
(the T2K term loop, survival/promotion outcomes) as ordinary Python control flow.

:class:`ChargenSession` drives that generator and applies one of two traversal
policies over the *same* flow:

* **quick**    — auto-resolves rolls, info, and non-essential choices; pauses only
  on steps flagged ``essential`` (the genuine decisions).
* **faithful** — pauses on every step, surfacing each roll and choice.

This keeps a single source of truth for a system's flow; the mode only changes
which steps stop for the user.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Generator
from dataclasses import dataclass, field

from ..dice import RollResult, evaluate
from .model import CharacterDraft, Step, StepKind, StepResult

QUICK = "quick"
FAITHFUL = "faithful"

# A flow: given the shared context, yields Steps and is sent StepResults back.
Flow = Generator[Step, StepResult, None]
FlowFactory = Callable[["ChargenContext"], Flow]


@dataclass
class ChargenContext:
    """Shared state handed to a system flow: the draft it fills in, the system data
    accessor it reads options from, and the dice/RNG it rolls with. The roller and
    rng are injectable so flows can be unit-tested deterministically."""

    draft: CharacterDraft
    data: object = None                                  # system accessor (e.g. T2KData)
    roll: Callable[[str], RollResult] = evaluate
    rng: random.Random = field(default_factory=random.SystemRandom)

    def log(self, message: str) -> None:
        self.draft.log.append(message)


class ChargenSession:
    """Drives a flow generator under a traversal policy. The cog reads
    :attr:`current` to render the step awaiting the user, and calls
    :meth:`resolve` from its button/select callbacks to advance."""

    def __init__(
        self,
        flow_factory: FlowFactory,
        *,
        mode: str,
        draft: CharacterDraft,
        data: object = None,
        roller: Callable[[str], RollResult] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if mode not in (QUICK, FAITHFUL):
            raise ValueError(f"mode must be {QUICK!r} or {FAITHFUL!r}, got {mode!r}")
        self.mode = mode
        # Kept so the session can rebuild itself from scratch for back() (replay).
        self._flow_factory = flow_factory
        self._game = draft.game
        self._data = data
        self._roller = roller or evaluate
        self._rng = rng or random.SystemRandom()
        self._replay: list[StepResult] = []   # recorded results to re-feed on back()
        self._checkpoints: list[int] = []      # history length at each live pause
        self.history: list[StepResult] = []
        self.ctx = ChargenContext(draft=draft, data=data, roll=self._roller, rng=self._rng)
        self._gen: Flow = flow_factory(self.ctx)
        self.current: Step | None = None
        self._begin()

    # --- public surface ----------------------------------------------------

    @property
    def draft(self) -> CharacterDraft:
        return self.ctx.draft

    @property
    def complete(self) -> bool:
        return self.draft.complete

    @property
    def can_back(self) -> bool:
        """True if there's a previous user-facing step to return to."""
        return not self.complete and len(self._checkpoints) >= 2

    def resolve(self, value: str | None = None) -> Step | None:
        """Resolve the paused step the user is looking at and advance. ``value`` is
        the chosen option's value for a CHOICE step (ignored for ROLL/INFO). Returns
        the next step to present, or ``None`` when finished. An invalid choice value
        is a no-op (the same step is returned, still awaiting input)."""
        step = self.current
        if step is None:
            return None
        if step.kind == StepKind.CHOICE:
            opt = next((o for o in step.options if o.value == value), None)
            if opt is None:
                return step  # invalid selection — keep prompting
            result = StepResult(step.id, value=opt.value, detail=opt.label)
        else:
            result = self._auto_result(step)  # ROLL → roll now; INFO → ack
        self.history.append(result)
        self.current = None
        self._drive(self._send_to_gen(result))
        return self.current

    def back(self) -> Step | None:
        """Return to the previous user-facing step by rebuilding the session and
        replaying every recorded answer up to that point (rolls included, so the
        earlier character is reproduced exactly). No-op if there's nowhere to go."""
        if not self.can_back:
            return self.current
        replay = self.history[:self._checkpoints[-2]]
        self.ctx = ChargenContext(
            draft=CharacterDraft(game=self._game),
            data=self._data, roll=self._roller, rng=self._rng,
        )
        self._gen = self._flow_factory(self.ctx)
        self.history = []
        self._checkpoints = []
        self._replay = list(replay)
        self.current = None
        self._begin()
        return self.current

    # --- internals ---------------------------------------------------------

    def _begin(self) -> None:
        """Prime the generator to its first yielded step, then drive forward."""
        try:
            step: Step | None = next(self._gen)
        except StopIteration:
            self._finish()
            return
        self._drive(step)

    def _drive(self, step: Step | None) -> None:
        """Advance through steps: take each result from the replay queue if one is
        pending, else auto-resolve non-pausing steps; stop at a live pause (sets
        ``current``) or run to the end. A checkpoint is recorded at every step that
        pauses live — including while replaying — so back() can target it again."""
        while step is not None:
            if self._replay:
                if self._should_pause(step):
                    self._checkpoints.append(len(self.history))
                result = self._replay.pop(0)
            elif self._should_pause(step):
                self.current = step
                self._checkpoints.append(len(self.history))
                return
            else:
                result = self._auto_result(step)
            self.history.append(result)
            step = self._send_to_gen(result)
        self._finish()
        self.current = None

    def _should_pause(self, step: Step) -> bool:
        if self.mode == FAITHFUL:
            return True
        # quick: only genuine decisions stop for the user.
        return step.kind == StepKind.CHOICE and step.essential

    def _auto_result(self, step: Step) -> StepResult:
        """Resolve a step without user input: roll a ROLL, pick a non-essential
        CHOICE at random, acknowledge an INFO."""
        if step.kind == StepKind.ROLL:
            return self._roll(step)
        if step.kind == StepKind.CHOICE:
            if not step.options:
                return StepResult(step.id, value="")
            opt = self.ctx.rng.choice(step.options)
            return StepResult(step.id, value=opt.value, detail=opt.label)
        return StepResult(step.id, value="ok")

    def _roll(self, step: Step) -> StepResult:
        rr = self.ctx.roll(step.roll_spec)
        return StepResult(step.id, value=rr.breakdown(), total=rr.total, detail=rr.breakdown())

    def _send_to_gen(self, result: StepResult) -> Step | None:
        try:
            return self._gen.send(result)
        except StopIteration:
            return None

    def _finish(self) -> None:
        self.draft.complete = True
