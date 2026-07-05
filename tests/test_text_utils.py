"""Tests for lorehound.text_utils — the ligature repair in particular."""

import unittest

from lorehound.text_utils import (
    _wordset,
    normalize_ligatures,
    repair_ligatures,
    strip_control_chars,
)

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


class TestNormalizeLigatures(unittest.TestCase):
    """Composed ligature glyphs → ASCII (dictionary-free, always applied)."""

    def test_maps_each_glyph(self):
        self.assertEqual(normalize_ligatures("eﬀect"), "effect")
        self.assertEqual(normalize_ligatures("ﬁre"), "fire")
        self.assertEqual(normalize_ligatures("reﬂect"), "reflect")
        self.assertEqual(normalize_ligatures("aﬃnity"), "affinity")     # ﬃ → ffi
        self.assertEqual(normalize_ligatures("baﬄe"), "baffle")         # ﬄ → ffl
        self.assertEqual(normalize_ligatures("Pathﬁnder"), "Pathfinder")

    def test_plain_text_untouched(self):
        self.assertEqual(normalize_ligatures("no ligatures here"), "no ligatures here")
        self.assertEqual(normalize_ligatures(""), "")

    def test_normalised_text_tokenises_for_search(self):
        from lorehound.search_index import tokenize
        self.assertEqual(tokenize(normalize_ligatures("the eﬀect of ﬁre")),
                         ["the", "effect", "of", "fire"])


class TestStripControlChars(unittest.TestCase):
    """Delete stray C0 control bytes the extractor leaves mid-text (\\x08/\\x07),
    keeping the structural whitespace \\t \\n \\r."""

    def test_deletes_corpus_offenders(self):
        # \x08 (backspace) and \x07 (bell) are the bytes actually found in the library.
        self.assertEqual(strip_control_chars("LETHARGY POISON\x08"), "LETHARGY POISON")
        self.assertEqual(strip_control_chars("Introduction\x08 4"), "Introduction 4")
        self.assertEqual(strip_control_chars("bell\x07gone"), "bellgone")

    def test_deletes_other_c0_and_del(self):
        self.assertEqual(strip_control_chars("a\x00b\x1f\x7fc"), "abc")

    def test_preserves_structural_whitespace(self):
        # newlines drive chunking; tabs/carriage-returns are structural — keep them all.
        self.assertEqual(strip_control_chars("l1\nl2\tcol\rx"), "l1\nl2\tcol\rx")

    def test_plain_text_untouched(self):
        self.assertEqual(strip_control_chars("no control chars here"), "no control chars here")
        self.assertEqual(strip_control_chars(""), "")

    def test_stripped_word_tokenises_as_one(self):
        # a control char mid-word otherwise splits the search token in two.
        from lorehound.search_index import tokenize
        self.assertEqual(tokenize(strip_control_chars("eff\x08ect")), ["effect"])
