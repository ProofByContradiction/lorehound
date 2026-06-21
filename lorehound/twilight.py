"""Twilight 2000 (4th edition) dice mechanics.

Rules per the T2K 4E core book (chapters 3–4):

* Attributes and skills are rated A-D, mapping to dice:
      A = D12, B = D10, C = D8, D = D6   (A is best)
* A check rolls the attribute die + the skill die together. Each die showing
  6–9 counts as one SUCCESS and 10+ as TWO ("multiple successes"). One success =
  you succeed; extra successes improve the result. Untrained rolls the attribute
  die alone.
* A die showing 1 is flagged because a 1 matters when you PUSH a roll.
* Ammo dice are D6s added to a ranged attack (full auto). Each 6+ is a success
  (extra hit), like a base die. Ammunition spent = the sum of the ammo dice
  PLUS one (the base round) — p. 63. The 1 face is the "jam" symbol: on a PUSH,
  each 1 (base or ammo) lowers the weapon's reliability by 1, and two or more 1s
  after a push jam the weapon. (We surface the 1s; we don't model pushing yet.)
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
    def successes(self) -> int:
        # T2K 4E: 6–9 = one success; 10+ = TWO ("multiple successes" — only
        # possible on a D10/D12). Max two per die, four per attribute+skill roll.
        if self.value >= 10:
            return 2
        if self.value >= SUCCESS_THRESHOLD:
            return 1
        return 0

    @property
    def is_success(self) -> bool:
        return self.successes >= 1

    @property
    def is_one(self) -> bool:
        return self.value == 1

    @property
    def rating(self) -> str:
        """Letter rating (A–D) for this die size, or '—' if non-standard."""
        return SIDES_TO_RATING.get(self.sides, "—")


@dataclass
class SkillRollResult:
    dice: list[DieOutcome]
    successes: int  # from the attribute + skill dice (the attack roll itself)
    ones: int
    ammo: "AmmoRollResult | None" = None  # optional ammo dice rolled with the attack

    @property
    def succeeded(self) -> bool:
        return self.successes >= 1

    @property
    def ammo_hits(self) -> int:
        """Extra hits from ammo dice (each 6). Zero when no ammo was rolled."""
        return self.ammo.extra_hits if self.ammo else 0

    @property
    def ammo_ones(self) -> int:
        return self.ammo.ones if self.ammo else 0

    @property
    def jam_ones(self) -> int:
        """1s on base + ammo dice. On a push, each costs 1 reliability; two or
        more after a push jam the weapon (T2K 4E, p. 63)."""
        return self.ones + self.ammo_ones

    @property
    def rounds_spent(self) -> int | None:
        """Ammunition used = sum of the ammo dice + 1, or None if no ammo dice."""
        return self.ammo.rounds_spent if self.ammo else None

    @property
    def total_successes(self) -> int:
        """Successes including ammo 6s — but only when the attack hits.

        A miss still spends the ammo, yet its 6s add no hits.
        """
        return self.successes + self.ammo_hits if self.succeeded else 0

    @property
    def can_push_warn(self) -> bool:
        """Whether to remind the player that 1s bite on a push."""
        return self.ones > 0


def skill_check(
    attribute: str, skill: str | None = None, ammo: int | None = None
) -> SkillRollResult:
    """Roll an attribute die plus an optional skill die and count successes.

    If ``ammo`` is a positive count, that many D6 ammo dice are rolled alongside
    the check (full-auto / ammo expenditure). Each 6 is an extra hit that adds to
    the attack's successes when the check hits; see ``ammo_dice``.
    """
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

    successes = sum(d.successes for d in dice)
    ones = sum(1 for d in dice if d.is_one)
    ammo_result = ammo_dice(ammo) if ammo else None
    return SkillRollResult(
        dice=dice, successes=successes, ones=ones, ammo=ammo_result
    )


@dataclass
class AmmoRollResult:
    rolls: list[int] = field(default_factory=list)

    @property
    def extra_hits(self) -> int:
        """Each 6 on an ammo die is a success (extra hit), like any base die."""
        return sum(1 for r in self.rolls if r >= 6)

    @property
    def ones(self) -> int:
        """Ammo dice showing 1 (the jam symbol) — threaten weapon reliability."""
        return sum(1 for r in self.rolls if r == 1)

    @property
    def total(self) -> int:
        return sum(self.rolls)

    @property
    def rounds_spent(self) -> int:
        """T2K 4E: ammunition used = sum of the ammo dice + 1 (the base round)."""
        return self.total + 1


def ammo_dice(count: int) -> AmmoRollResult:
    """Roll ``count`` ammo dice (D6)."""
    if count < 1:
        raise TwilightError("Roll at least one ammo die.")
    if count > 50:
        raise TwilightError("That's a lot of ammo dice (max 50).")
    return AmmoRollResult(rolls=[_rng.randint(1, 6) for _ in range(count)])
