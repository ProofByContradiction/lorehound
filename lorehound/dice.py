"""Generic dice engine for Lorehound.

Pure Python, no Discord or external dependencies, so it can be unit-tested in
isolation. Supports standard polyhedral dice and simple dice-notation strings
like ``2d6+1``, ``d20``, ``3d8-2``, or compound expressions like ``2d6 + 1d8 - 1``.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

# Use the OS CSPRNG so rolls aren't predictable / seedable by players.
_rng = random.SystemRandom()

# Dice we consider "standard" for an RPG helper. Arbitrary sides are still
# allowed by the parser; this list just powers the quick-roll slash commands.
STANDARD_DICE = (4, 6, 8, 10, 12, 20, 100)

MAX_DICE = 100          # guard against /roll 10000d6 spam
MAX_SIDES = 1000


class DiceError(ValueError):
    """Raised for malformed dice expressions or out-of-range requests."""


@dataclass
class DiceGroup:
    """One ``NdS`` term within an expression."""

    count: int
    sides: int
    rolls: list[int] = field(default_factory=list)

    @property
    def subtotal(self) -> int:
        return sum(self.rolls)

    def __str__(self) -> str:
        return f"{self.count}d{self.sides} {self.rolls} = {self.subtotal}"


@dataclass
class RollResult:
    """The outcome of evaluating a full expression."""

    expression: str
    groups: list[DiceGroup]
    modifier: int
    total: int

    def breakdown(self) -> str:
        """Human-readable breakdown, e.g. ``2d6 [3, 5] + 1d8 [7] + 2``."""
        parts = [f"{g.count}d{g.sides} {g.rolls}" for g in self.groups]
        if self.modifier:
            parts.append(f"{self.modifier:+d}")
        return " + ".join(parts).replace("+ -", "- ")


def roll_dice(count: int, sides: int) -> DiceGroup:
    """Roll ``count`` dice with ``sides`` sides each."""
    if count < 1:
        raise DiceError("You must roll at least one die.")
    if count > MAX_DICE:
        raise DiceError(f"That's too many dice (max {MAX_DICE}).")
    if sides < 2:
        raise DiceError("A die needs at least 2 sides.")
    if sides > MAX_SIDES:
        raise DiceError(f"That's too many sides (max {MAX_SIDES}).")
    rolls = [_rng.randint(1, sides) for _ in range(count)]
    return DiceGroup(count=count, sides=sides, rolls=rolls)


# Matches a single term: optional count, 'd', sides  -> e.g. d20, 2d6, 10D8
_TERM_RE = re.compile(r"^(\d*)[dD](\d+)$")


def evaluate(expression: str) -> RollResult:
    """Parse and roll a dice expression.

    Accepts terms joined by ``+``/``-``: dice terms (``NdS``) and flat integer
    modifiers. Whitespace is ignored. Examples: ``2d6+1``, ``d20``,
    ``3d8 - 2``, ``2d6 + 1d8 + 3``.
    """
    if not expression or not expression.strip():
        raise DiceError("Give me something to roll, e.g. `2d6+1`.")

    # Normalise: collapse whitespace, then split into signed tokens.
    cleaned = expression.replace(" ", "")
    # Insert a leading '+' so the first term is captured by the same regex.
    if not cleaned.startswith(("+", "-")):
        cleaned = "+" + cleaned

    token_re = re.compile(r"([+-])([^+-]+)")
    pos = 0
    groups: list[DiceGroup] = []
    modifier = 0
    matched_any = False

    for m in token_re.finditer(cleaned):
        if m.start() != pos:
            raise DiceError(f"I couldn't parse `{expression}`.")
        pos = m.end()
        matched_any = True
        sign = 1 if m.group(1) == "+" else -1
        body = m.group(2)

        term = _TERM_RE.match(body)
        if term:
            count = int(term.group(1)) if term.group(1) else 1
            sides = int(term.group(2))
            group = roll_dice(count, sides)
            if sign < 0:
                # Negative dice term: subtract the rolled total.
                group.rolls = [-r for r in group.rolls]
            groups.append(group)
        elif body.isdigit():
            modifier += sign * int(body)
        else:
            raise DiceError(f"I couldn't parse the term `{body}` in `{expression}`.")

    if not matched_any or pos != len(cleaned):
        raise DiceError(f"I couldn't parse `{expression}`.")
    if not groups and modifier == 0:
        raise DiceError("That didn't contain any dice to roll.")

    total = sum(g.subtotal for g in groups) + modifier
    return RollResult(
        expression=expression.strip(),
        groups=groups,
        modifier=modifier,
        total=total,
    )
