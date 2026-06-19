"""Twilight 2000 (4th edition) dice mechanics.

NOTE: These encode my current understanding of the T2K 4E rules. Since the whole
point of Lorehound is rules accuracy, please confirm/correct the specifics
(especially ammo dice) and I'll adjust. What I've assumed:

* Attributes and skills are rated A-D, mapping to dice:
      A = D12, B = D10, C = D8, D = D6   (A is best)
* A check rolls the attribute die + the skill die together. Each die showing
  6 or higher counts as one SUCCESS. One success = you succeed; extra successes
  improve the result. A skill used untrained rolls the attribute die alone.
* A die showing 1 is flagged because a 1 matters when you PUSH a roll.
* Ammo dice are D6s rolled for automatic fire / ammo expenditure. Each 6 is
  treated as an extra hit (success). The depletion rule is the part I'm least
  sure of -- tell me how your table tracks rounds spent and I'll wire it in.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

_rng = random.SystemRandom()

# Letter rating -> die size. A is the best die.
RATING_TO_SIDES = {"A": 12, "B": 10, "C": 8, "D": 6}
SIDES_TO_RATING = {v: k for k, v in RATING_TO_SIDES.items()}

SUCCESS_THRESHOLD = 6  # a die showing this or higher is a success


class TwilightError(ValueError):
    """Raised for invalid Twilight 2000 roll inputs."""


def rating_to_sides(rating: str) -> int:
    """Convert an 'A'-'D' rating (or a raw die size like '12') to a die size."""
    key = rating.strip().upper()
    if key in RATING_TO_SIDES:
        return RATING_TO_SIDES[key]
    # Allow raw die sizes too: "d8", "8", "D10".
    digits = key.lstrip("D")
    if digits.isdigit() and int(digits) in SIDES_TO_RATING:
        return int(digits)
    raise TwilightError(
        f"`{rating}` isn't a valid rating. Use A, B, C, or D (or a die like d8)."
    )


@dataclass
class DieOutcome:
    label: str          # e.g. "attribute (D10)"
    sides: int
    value: int

    @property
    def is_success(self) -> bool:
        return self.value >= SUCCESS_THRESHOLD

    @property
    def is_one(self) -> bool:
        return self.value == 1


@dataclass
class SkillRollResult:
    dice: list[DieOutcome]
    successes: int
    ones: int

    @property
    def succeeded(self) -> bool:
        return self.successes >= 1

    @property
    def can_push_warn(self) -> bool:
        """Whether to remind the player that 1s bite on a push."""
        return self.ones > 0


def skill_check(attribute: str, skill: str | None = None) -> SkillRollResult:
    """Roll an attribute die plus an optional skill die and count successes."""
    dice: list[DieOutcome] = []

    attr_sides = rating_to_sides(attribute)
    dice.append(
        DieOutcome("attribute", attr_sides, _rng.randint(1, attr_sides))
    )

    if skill is not None and skill.strip():
        skill_sides = rating_to_sides(skill)
        dice.append(
            DieOutcome("skill", skill_sides, _rng.randint(1, skill_sides))
        )

    successes = sum(1 for d in dice if d.is_success)
    ones = sum(1 for d in dice if d.is_one)
    return SkillRollResult(dice=dice, successes=successes, ones=ones)


@dataclass
class AmmoRollResult:
    rolls: list[int] = field(default_factory=list)

    @property
    def extra_hits(self) -> int:
        """6s = extra hits (current assumption)."""
        return sum(1 for r in self.rolls if r >= 6)

    @property
    def ones(self) -> int:
        return sum(1 for r in self.rolls if r == 1)

    @property
    def total(self) -> int:
        return sum(self.rolls)


def ammo_dice(count: int) -> AmmoRollResult:
    """Roll ``count`` ammo dice (D6)."""
    if count < 1:
        raise TwilightError("Roll at least one ammo die.")
    if count > 50:
        raise TwilightError("That's a lot of ammo dice (max 50).")
    return AmmoRollResult(rolls=[_rng.randint(1, 6) for _ in range(count)])
