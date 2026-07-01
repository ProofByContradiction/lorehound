"""Tests for lorehound.stat_boxes — parsing boxed spell/feat entries from the
extracted markdown."""

import unittest

from lorehound.stat_boxes import StatBox, parse_stat_boxes

MD = """\
[[page 339]]
##### **FIREBALL SPELL 3**

**Traditions** arcane, primal
**Cast** somatic, verbal
**Range** 500 feet; **Area** 20-foot burst
**Saving Throw** basic Reflex
A roaring blast of fire appears at a spot you designate, dealing
6d6 fire damage.
**Heightened (+1)** The damage increases by 2d6.

[[page 340]]
_**paizo.co**_ _**,**_ _**Wexel**_ _**Wiggyjiggyjed**_ _**ead@g**_ _**ail.co**_
Core Rulebook
_**l**_ _**d**_ _**18**_ _**2023**_
##### **POWER ATTACK FEAT 1**

**Requirements** You are wielding a melee weapon.
You unleash a particularly powerful attack.
"""


class TestParseStatBoxes(unittest.TestCase):
    def setUp(self):
        self.boxes = {b.name: b for b in parse_stat_boxes(MD)}

    def test_finds_both_boxes_with_kind_and_level(self):
        self.assertEqual(set(self.boxes), {"FIREBALL", "POWER ATTACK"})
        self.assertEqual((self.boxes["FIREBALL"].kind, self.boxes["FIREBALL"].level), ("SPELL", 3))
        self.assertEqual((self.boxes["POWER ATTACK"].kind, self.boxes["POWER ATTACK"].level), ("FEAT", 1))

    def test_fields_parsed_including_two_on_one_line(self):
        f = dict(self.boxes["FIREBALL"].fields)
        self.assertEqual(f["Traditions"], "arcane, primal")
        self.assertEqual(f["Range"], "500 feet")       # stops at **Area**, trailing ; dropped
        self.assertEqual(f["Area"], "20-foot burst")
        self.assertEqual(f["Saving Throw"], "basic Reflex")

    def test_description_excludes_field_lines(self):
        desc = self.boxes["FIREBALL"].description
        self.assertIn("roaring blast of fire", desc)
        self.assertNotIn("Traditions", desc)

    def test_watermark_and_running_header_filtered(self):
        # the paizo watermark / date-stamp / "Core Rulebook" between pages must not
        # leak in as fields on the following box
        labels = [label for label, _ in self.boxes["POWER ATTACK"].fields]
        self.assertEqual(labels, ["Requirements"])
        self.assertNotIn("paizo.co", labels)

    def test_page_tracked_from_nearest_marker(self):
        self.assertEqual(self.boxes["FIREBALL"].page, 339)
        self.assertEqual(self.boxes["POWER ATTACK"].page, 340)

    def test_category_routes_feat_vs_spell(self):
        self.assertEqual(self.boxes["FIREBALL"].category, "spell")
        self.assertEqual(self.boxes["POWER ATTACK"].category, "feat")

    def test_no_boxes_in_plain_text(self):
        self.assertEqual(parse_stat_boxes("Just some prose with no boxes.\n## A Heading\n"), [])

    def test_returns_statbox_instances(self):
        self.assertIsInstance(self.boxes["FIREBALL"], StatBox)

    def test_wrapped_field_value_absorbs_continuation(self):
        # a field value that wraps to the next visual line keeps its tail instead of
        # leaking the first word into the description
        md = ("##### **HEAL SPELL 1**\n"
              "**Targets** 1 willing living creature or 1 undead\n"
              "creature\n"
              "You channel positive energy to heal.\n")
        box = parse_stat_boxes(md)[0]
        self.assertEqual(dict(box.fields)["Targets"],
                         "1 willing living creature or 1 undead creature")
        self.assertTrue(box.description.startswith("You channel"))
        self.assertNotIn("creature You", box.description)

    def test_repeated_field_label_keeps_first(self):
        # interleaved-column bleed can repeat **Prerequisites** — keep the first
        md = ("##### **DREAD STRIKER FEAT 4**\n"
              "**Prerequisites** master in Perception\n"
              "You capitalize on fear.\n"
              "**Prerequisites** Nimble Dodge\n")
        box = parse_stat_boxes(md)[0]
        prereqs = [v for k, v in box.fields if k == "Prerequisites"]
        self.assertEqual(prereqs, ["master in Perception"])
