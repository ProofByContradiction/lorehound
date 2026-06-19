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
        # Force determinism by checking the threshold logic across many rolls.
        for _ in range(200):
            res = skill_check("A", "A")
            expected = sum(1 for d in res.dice if d.value >= 6)
            self.assertEqual(res.successes, expected)

    def test_ammo_dice(self):
        res = ammo_dice(6)
        self.assertEqual(len(res.rolls), 6)
        self.assertTrue(all(1 <= r <= 6 for r in res.rolls))
        self.assertEqual(res.extra_hits, sum(1 for r in res.rolls if r >= 6))


if __name__ == "__main__":
    unittest.main()
