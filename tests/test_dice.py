"""Unit tests for the pure dice engine — no Discord or network needed.

Run with:  python -m pytest   (or)   python -m unittest
"""

import unittest

from lorehound.dice import DiceError, evaluate, roll_dice
from lorehound.twilight import ammo_dice, rating_to_sides, skill_check


class TestGenericDice(unittest.TestCase):
    def test_single_die_range(self):
        for _ in range(200):
            g = roll_dice(1, 6)
            self.assertEqual(len(g.rolls), 1)
            self.assertTrue(1 <= g.rolls[0] <= 6)

    def test_multiple_dice_count(self):
        g = roll_dice(5, 12)
        self.assertEqual(len(g.rolls), 5)
        self.assertTrue(all(1 <= r <= 12 for r in g.rolls))

    def test_notation_with_modifier(self):
        r = evaluate("2d6+3")
        self.assertEqual(len(r.groups), 1)
        self.assertEqual(r.groups[0].count, 2)
        self.assertEqual(r.groups[0].sides, 6)
        self.assertEqual(r.modifier, 3)
        # total = sum of two d6 (2..12) + 3
        self.assertTrue(5 <= r.total <= 15)

    def test_compound_expression(self):
        r = evaluate("2d6 + 1d8 + 1")
        self.assertEqual(len(r.groups), 2)
        self.assertEqual(r.modifier, 1)

    def test_implicit_single(self):
        r = evaluate("d20")
        self.assertEqual(r.groups[0].count, 1)
        self.assertEqual(r.groups[0].sides, 20)

    def test_negative_modifier(self):
        r = evaluate("1d4-1")
        self.assertEqual(r.modifier, -1)
        self.assertTrue(0 <= r.total <= 3)

    def test_too_many_dice(self):
        with self.assertRaises(DiceError):
            roll_dice(99999, 6)

    def test_garbage_raises(self):
        for bad in ["", "abc", "2d", "d", "2x6", "2d6++1"]:
            with self.assertRaises(DiceError):
                evaluate(bad)


class TestTwilight(unittest.TestCase):
    def test_rating_mapping(self):
        self.assertEqual(rating_to_sides("A"), 12)
        self.assertEqual(rating_to_sides("d8"), 8)
        self.assertEqual(rating_to_sides("D"), 6)

    def test_skill_check_two_dice(self):
        res = skill_check("A", "C")  # d12 + d8
        self.assertEqual(len(res.dice), 2)
        self.assertEqual({d.sides for d in res.dice}, {12, 8})

    def test_skill_check_untrained_one_die(self):
        res = skill_check("B")  # d10 only
        self.assertEqual(len(res.dice), 1)
        self.assertEqual(res.dice[0].sides, 10)

    def test_success_counting(self):
        # T2K 4E: 6–9 = 1 success, 10+ = 2 successes.
        def expect(v):
            return 2 if v >= 10 else (1 if v >= 6 else 0)

        for _ in range(300):
            res = skill_check("A", "A")
            expected = sum(expect(d.value) for d in res.dice)
            self.assertEqual(res.successes, expected)

    def test_success_tiers(self):
        from lorehound.twilight import DieOutcome

        cases = {1: 0, 5: 0, 6: 1, 7: 1, 8: 1, 9: 1, 10: 2, 11: 2, 12: 2}
        for value, expected in cases.items():
            self.assertEqual(DieOutcome("x", 12, value).successes, expected)

    def test_die_rating_from_sides(self):
        from lorehound.twilight import DieOutcome

        self.assertEqual(DieOutcome("attribute", 12, 7).rating, "A")
        self.assertEqual(DieOutcome("attribute", 10, 3).rating, "B")
        self.assertEqual(DieOutcome("skill", 8, 6).rating, "C")
        self.assertEqual(DieOutcome("skill", 6, 1).rating, "D")

    def test_skill_check_dice_carry_rating(self):
        res = skill_check("A", "C")  # d12 + d8 -> A + C
        self.assertEqual({d.rating for d in res.dice}, {"A", "C"})

    def test_ammo_dice(self):
        res = ammo_dice(6)
        self.assertEqual(len(res.rolls), 6)
        self.assertTrue(all(1 <= r <= 6 for r in res.rolls))
        self.assertEqual(res.extra_hits, sum(1 for r in res.rolls if r >= 6))

    def test_skill_check_no_ammo_by_default(self):
        res = skill_check("B", "C")
        self.assertIsNone(res.ammo)
        self.assertEqual(res.ammo_hits, 0)
        # With no ammo, the total matches the base successes (when it hits).
        self.assertEqual(res.total_successes, res.successes if res.succeeded else 0)

    def test_ammo_rounds_spent_is_sum_plus_one(self):
        from lorehound.twilight import AmmoRollResult

        res = AmmoRollResult(rolls=[6, 3, 1, 5])
        self.assertEqual(res.total, 15)
        self.assertEqual(res.rounds_spent, 16)  # sum + 1 (the base round)
        self.assertEqual(res.ones, 1)

    def test_skillcheck_jam_ones_counts_base_and_ammo(self):
        from lorehound.twilight import AmmoRollResult, DieOutcome, SkillRollResult

        res = SkillRollResult(
            dice=[DieOutcome("attribute", 10, 1)],  # one base 1
            successes=0,
            ones=1,
            ammo=AmmoRollResult(rolls=[1, 1, 4]),   # two ammo 1s
        )
        self.assertEqual(res.ammo_ones, 2)
        self.assertEqual(res.jam_ones, 3)            # base + ammo 1s
        self.assertEqual(res.rounds_spent, 1 + 1 + 4 + 1)

    def test_skill_check_with_ammo(self):
        for _ in range(300):
            res = skill_check("A", "C", ammo=4)  # d12 + d8 + 4 ammo D6
            self.assertIsNotNone(res.ammo)
            self.assertEqual(len(res.ammo.rolls), 4)
            self.assertEqual(
                res.ammo_hits, sum(1 for r in res.ammo.rolls if r >= 6)
            )
            # Base successes never include ammo dice...
            base = sum(d.successes for d in res.dice)
            self.assertEqual(res.successes, base)
            # ...but ammo 6s fold into the total only on a hit.
            if res.succeeded:
                self.assertEqual(res.total_successes, base + res.ammo_hits)
            else:
                self.assertEqual(res.total_successes, 0)

    def test_skill_check_ammo_count_validated(self):
        from lorehound.twilight import TwilightError

        with self.assertRaises(TwilightError):
            skill_check("A", "C", ammo=999)
        # Zero/None ammo is simply "no ammo dice", not an error.
        self.assertIsNone(skill_check("A", ammo=0).ammo)


if __name__ == "__main__":
    unittest.main()
