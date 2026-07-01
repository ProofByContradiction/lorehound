"""Tests for the geometric spell/feat box reconstructor (lorehound.stat_box_extract).

The public ``page_spell_boxes`` needs a PDF page, so these exercise the pure
span-level helpers with synthetic span dicts."""

import unittest

from lorehound.stat_box_extract import _box_lines, _is_bold, _is_heading_span


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
