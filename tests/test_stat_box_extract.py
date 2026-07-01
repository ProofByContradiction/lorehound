"""Tests for the geometric spell/feat box reconstructor (lorehound.stat_box_extract).

The public ``page_spell_boxes`` needs a PDF page, so these exercise the pure
span-level helpers with synthetic span dicts."""

import unittest

from lorehound.stat_box_extract import (
    _TAB_WORDS,
    _box_lines,
    _derive_chrome,
    _detect_box_heads,
    _is_bold,
    _is_heading_span,
)


def span(text, x, y, size=8.0, font="GoodOT", flags=0):
    return {"text": text, "origin": (x, y), "size": size, "font": font, "flags": flags}


class TestHeadingDetection(unittest.TestCase):
    def test_display_bold_uppercase_is_heading(self):
        self.assertTrue(_is_heading_span(span("HEAL", 306, 136, size=12.0, font="GoodOT-CondBold")))
        self.assertTrue(_is_heading_span(span("MAGIC MISSILE", 68, 200, size=12.0, font="GoodOT-CondBold")))

    def test_body_and_wrong_style_are_not_headings(self):
        self.assertFalse(_is_heading_span(span("Traditions", 68, 150, size=8.0, font="GoodOT")))
        self.assertFalse(_is_heading_span(span("Heal", 306, 136, size=12.0, font="GoodOT-CondBold")))  # not caps
        self.assertFalse(_is_heading_span(span("HEAL", 306, 136, size=12.0, font="GoodOT")))  # wrong font
        self.assertFalse(_is_heading_span(span("SPELL 1", 480, 136, size=12.0, font="GoodOT-CondBold")))  # has digit


class TestDeriveBoxHeadStyle(unittest.TestCase):
    """#62: the box-heading font is derived from the page's own signature (a name
    span sharing its line with a KIND+level token), not hardcoded to Paizo's."""

    def test_derives_font_from_signature_for_any_publisher(self):
        # A book that uses a completely different display font must still work.
        spans = [
            span("MAGIC MISSILE", 68, 200, size=12.0, font="AcmeDisplay-Bold"),
            span("SPELL 1", 200, 200, size=12.0, font="AcmeDisplay-Bold"),  # KIND, to the right
            span("You fire three darts.", 68, 214, size=8.0, font="Times"),
        ]
        fonts, lo, hi = _detect_box_heads(spans)
        self.assertIn("AcmeDisplay-Bold", fonts)
        self.assertLessEqual(lo, 12.0)
        self.assertGreaterEqual(hi, 12.0)
        # The derived (font, size) band then classifies the name as a heading.
        self.assertTrue(_is_heading_span(spans[0], fonts, lo, hi))
        self.assertFalse(_is_heading_span(spans[2], fonts, lo, hi))  # body

    def test_self_gates_without_a_kind_token(self):
        # All-caps text but no KIND+level anywhere → not a boxed page → produce nothing.
        spans = [
            span("CHAPTER TWO", 68, 100, size=12.0, font="AcmeDisplay-Bold"),
            span("Some prose here.", 68, 120, size=8.0, font="Times"),
        ]
        fonts, lo, hi = _detect_box_heads(spans)
        self.assertEqual(fonts, set())

    def test_kind_must_be_to_the_right_same_line(self):
        # A KIND token on a different line (below) is a running label, not this
        # name's level — so the name isn't treated as a box heading.
        spans = [
            span("MAGIC MISSILE", 68, 200, size=12.0, font="AcmeDisplay-Bold"),
            span("SPELL 1", 68, 260, size=9.0, font="Gin"),  # far below, not same line
        ]
        fonts, _, _ = _detect_box_heads(spans)
        self.assertEqual(fonts, set())


class TestDeriveChrome(unittest.TestCase):
    """#62 (increment B): page chrome (the vertical tab margin + running headers) is
    derived from the document's own ToC titles, not hardcoded to Paizo's Gin/_TAB_WORDS."""

    def _body(self):
        # Enough body text that "Times" is unambiguously the page's body font.
        return [
            span("You cast a spell that scorches your foes with roaring flame.",
                 68, 200, size=8.0, font="Times"),
            span("The blast fills the area and everyone must attempt a save.",
                 68, 212, size=8.0, font="Times"),
        ]

    def test_running_header_font_derived_as_chrome(self):
        raw = self._body() + [span("SPELLS", 300, 40, size=8.0, font="Gill-Chrome")]
        chrome_fonts, tab_words = _derive_chrome(raw, frozenset({"Spells"}))
        self.assertIn("Gill-Chrome", chrome_fonts)   # font of the running header
        self.assertIn("Gin", chrome_fonts)           # Paizo default retained
        self.assertIn("Spells", tab_words)           # ToC word folded into tab words

    def test_no_toc_falls_back_to_paizo_defaults(self):
        chrome_fonts, tab_words = _derive_chrome(self._body(), frozenset())
        self.assertEqual(chrome_fonts, {"Gin"})
        self.assertEqual(tab_words, set(_TAB_WORDS))

    def test_body_font_never_flagged_even_if_it_matches_a_title(self):
        # A body span whose text happens to equal a chapter title must NOT drop the
        # body font — that would delete real content.
        raw = self._body() + [span("Equipment", 68, 224, size=8.0, font="Times")]
        chrome_fonts, _ = _derive_chrome(raw, frozenset({"Equipment"}))
        self.assertEqual(chrome_fonts, {"Gin"})       # Times (body) not added

    def test_heading_font_never_flagged_even_if_it_matches_a_title(self):
        # A box-name heading whose text matches a ToC entry must not drop the heading
        # font (which would erase every box name in that font).
        raw = self._body() + [
            span("MAGIC MISSILE", 68, 240, size=12.0, font="Disp-Bold"),
            span("SPELL 1", 200, 240, size=12.0, font="Disp-Bold"),  # KIND sibling
        ]
        chrome_fonts, _ = _derive_chrome(raw, frozenset({"Magic Missile"}))
        self.assertEqual(chrome_fonts, {"Gin"})       # Disp-Bold (heading) not added


class TestBoxLines(unittest.TestCase):
    def test_lines_grouped_by_y_and_bold_wrapped(self):
        spans = [
            span("Traditions", 68, 150, flags=16), span("divine, primal", 110, 150),
            span("Cast", 68, 162, flags=16), span("somatic, verbal", 95, 162),
            span("You channel positive energy.", 68, 176),
        ]
        lines = _box_lines(spans)
        self.assertEqual(lines[0], "**Traditions** divine, primal")
        self.assertEqual(lines[1], "**Cast** somatic, verbal")
        self.assertEqual(lines[2], "You channel positive energy.")

    def test_kind_level_span_dropped_from_body(self):
        # a stray "SPELL 1" inside the region isn't a field
        lines = _box_lines([span("SPELL 1", 480, 136), span("Range", 68, 150, flags=16),
                            span("30 feet", 95, 150)])
        self.assertEqual(lines, ["**Range** 30 feet"])

    def test_is_bold(self):
        self.assertTrue(_is_bold(span("x", 0, 0, flags=16)))
        self.assertTrue(_is_bold(span("x", 0, 0, font="GoodOT-Bold")))
        self.assertFalse(_is_bold(span("x", 0, 0, font="GoodOT")))
