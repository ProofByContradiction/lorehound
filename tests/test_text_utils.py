"""Tests for lorehound.text_utils — the ligature repair in particular."""

import unittest

from lorehound.text_utils import _wordset, repair_ligatures

# The repair needs a real system word list; CI containers may not have one, in
# which case it's a deliberate no-op (so the assertions below are skipped).
_HAS_DICT = len(_wordset()) >= 1000


@unittest.skipUnless(_HAS_DICT, "no system word list (/usr/share/dict/words)")
class TestRepairLigatures(unittest.TestCase):
    def test_repairs_dropped_fi_fl(self):
        self.assertEqual(repair_ligatures("6d6 fre damage"), "6d6 fire damage")
        self.assertEqual(repair_ligatures("basic Refex"), "basic Reflex")
        self.assertEqual(repair_ligatures("profciency"), "proficiency")
        self.assertEqual(repair_ligatures("infict"), "inflict")
        self.assertEqual(repair_ligatures("difcult"), "difficult")   # ffi double ligature

    def test_real_words_untouched(self):
        for w in ("from", "free", "after", "often", "front", "friendly", "free-form"):
            self.assertEqual(repair_ligatures(w), w)

    def test_feet_and_ft_protected(self):
        # web2 lacks "feet" — without protection the repair would yield "fleet"
        self.assertEqual(repair_ligatures("within 30 feet"), "within 30 feet")
        self.assertEqual(repair_ligatures("ft"), "ft")

    def test_case_preserved(self):
        self.assertEqual(repair_ligatures("Pathfnder"), "Pathfinder")
        self.assertEqual(repair_ligatures("Refex save"), "Reflex save")


class TestRepairLigaturesAlwaysSafe(unittest.TestCase):
    def test_never_crashes_returns_str(self):
        # with or without a dictionary, repair is total and returns a string
        self.assertIsInstance(repair_ligatures("any text with fre"), str)
        self.assertEqual(repair_ligatures(""), "")
