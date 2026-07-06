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

    def test_heightened_is_captured_as_a_field(self):
        # "Heightened (+1)" / "Heightened (3rd)" contain digits but are real fields —
        # they must not be dropped (nor leak into the description).
        md = ("##### **FIREBALL SPELL 3**\n"
              "**Saving Throw** basic Reflex\n"
              "A blast of fire deals 6d6 fire damage.\n"
              "**Heightened (+1)** The damage increases by 2d6.\n")
        box = parse_stat_boxes(md)[0]
        self.assertIn(("Heightened (+1)", "The damage increases by 2d6."), box.fields)
        self.assertNotIn("Heightened", box.description)

    def test_heightened_value_absorbs_capital_wrapped_sentence(self):
        # Regression: a Heightened value whose text wraps onto a new *sentence*
        # (capital-initial) must stay in the field, not orphan into the description.
        # This is Teleport's level-9 "...same solar system. Assuming you have accurate
        # knowledge..." — the capital "Assuming" used to leak into the description.
        md = ("##### **TELEPORT SPELL 6**\n"
              "**Range** 100 miles\n"
              "You and the targets are transported within range.\n"
              "**Heightened (9th)** You and the other targets can travel to any\n"
              "location on another planet within the same solar system.\n"
              "Assuming you have accurate knowledge of the location, you\n"
              "arrive on the new planet 100 miles off target.\n")
        box = parse_stat_boxes(md)[0]
        h9 = dict(box.fields)["Heightened (9th)"]
        self.assertIn("Assuming you have accurate knowledge", h9)
        self.assertTrue(h9.endswith("100 miles off target."))
        self.assertNotIn("Assuming", box.description)
        # the real description is untouched
        self.assertEqual(box.description,
                         "You and the targets are transported within range.")

    def test_capital_wrap_only_applies_to_heightened(self):
        # The any-case continuation is scoped to Heightened so PF's interleaved
        # multi-column feat bleed (capital "Reflexive Shield 6" fragments after a
        # non-Heightened field) is NOT absorbed into that field's value.
        md = ("##### **POWER ATTACK FEAT 1**\n"
              "**Requirements** You are wielding a melee weapon.\n"
              "Reflexive Shield 6\n"
              "You unleash a powerful attack.\n")
        box = parse_stat_boxes(md)[0]
        self.assertEqual(dict(box.fields)["Requirements"],
                         "You are wielding a melee weapon.")
        # the capital bleed line is not glued onto Requirements
        self.assertNotIn("Reflexive Shield", dict(box.fields)["Requirements"])

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


class TestGeneralizedKinds(unittest.TestCase):
    """#62 (increment C): box categories beyond the hardcoded spell/feat set are
    recovered when they recur (e.g. Pathfinder's HAZARD/ITEM boxes), routed to their
    own category so they aren't mislabelled as spells, and one-offs are ignored."""

    def test_recurring_novel_kind_recovered_with_own_category(self):
        md = ("##### **HIDDEN PIT HAZARD 3**\n**Complexity** simple\nA pit opens.\n\n"
              "##### **SPIKED PIT HAZARD 4**\n**Complexity** simple\nSpikes line it.\n\n"
              "##### **DROWNING PIT HAZARD 5**\n**Stealth** trained\nIt floods.\n")
        boxes = parse_stat_boxes(md)
        self.assertEqual(len(boxes), 3)
        self.assertTrue(all(b.kind == "HAZARD" for b in boxes))
        self.assertTrue(all(b.category == "hazard" for b in boxes))  # not "spell"

    def test_one_off_novel_kind_is_ignored(self):
        md = ("##### **FIREBALL SPELL 3**\n**Range** 500 feet\nA blast of fire.\n\n"
              "##### **ODD THING WIDGET 2**\n**Foo** bar\nA one-off heading.\n")
        names = {b.name for b in parse_stat_boxes(md)}
        self.assertIn("FIREBALL", names)        # known kind kept
        self.assertNotIn("ODD THING", names)    # single WIDGET → dropped

    def test_known_kinds_still_parse_at_any_count(self):
        # A lone SPELL box (count 1) is still recovered — known kinds never need to recur.
        boxes = parse_stat_boxes("##### **BLESS SPELL 1**\n**Range** 30 feet\nBless allies.\n")
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0].category, "spell")


class TestFeatSidebarBleed(unittest.TestCase):
    """PF's alphabetical feats-by-level sidebar bleeds <Name> <Level> fragments into a
    feat's description; the monotonic-alphabetical-run strip removes them (feats only)."""

    def test_alphabetical_run_stripped_prose_kept(self):
        md = ("##### **POWER ATTACK FEAT 1**\n"
              "Reactive Shield 1\nReflexive Shield 6\n"
              "You unleash a particularly powerful attack. Revealing Stab 6 Make a melee "
              "Strike. Savage Critical 18\nShatter Defenses 6 This counts as two attacks.\n")
        desc = parse_stat_boxes(md)[0].description
        for frag in ("Reactive Shield", "Reflexive Shield", "Revealing Stab",
                     "Savage Critical", "Shatter Defenses"):
            self.assertNotIn(frag, desc)
        self.assertIn("You unleash a particularly powerful attack", desc)
        self.assertIn("Make a melee Strike", desc)
        self.assertIn("This counts as two attacks", desc)

    def test_leading_prose_word_not_swallowed(self):
        # "...Make a Strike. The Incredible Ricochet 12" — "The" is prose, the fragment
        # is "Incredible Ricochet"; the run strip must keep "The Strike gains…".
        md = ("##### **EXACTING STRIKE FEAT 1**\n"
              "You make a controlled attack. Make a Strike. The Improved Twin Riposte 14 "
              "Incredible Aim 8 Incredible Ricochet 12 Intimidating Strike 2 Strike gains "
              "the following failure effect.\n")
        desc = parse_stat_boxes(md)[0].description
        self.assertNotIn("Incredible Ricochet", desc)
        self.assertNotIn("Improved Twin Riposte", desc)
        self.assertIn("The Strike gains the following failure effect", desc)

    def test_incidental_capital_number_below_threshold_kept(self):
        # A clean feat with a couple of incidental "<Cap> <num>" phrases (no long run)
        # must be left exactly as-is — the trigger needs a run of >=4.
        md = ("##### **WIDEN SPELL FEAT 1**\n"
              "You manipulate your spell. Add 5 feet to the radius. Around 3 foes.\n")
        desc = parse_stat_boxes(md)[0].description
        self.assertIn("Add 5 feet to the radius", desc)
        self.assertIn("Around 3 foes", desc)

    def test_only_feats_are_stripped_not_spells(self):
        # The sidebar bleed is a feat-layout artifact; a spell with the same shape is
        # left untouched (guards against over-eager stripping of legitimate spell text).
        md = ("##### **SOME SPELL SPELL 1**\n"
              "You cast it. Reactive Shield 1 Reflexive Shield 6 Revealing Stab 6 "
              "Savage Critical 18 It works.\n")
        desc = parse_stat_boxes(md)[0].description
        self.assertIn("Reactive Shield 1", desc)   # NOT stripped — spell, not feat


class TestType2FeatRecovery(unittest.TestCase):
    """Some PF feats lost their ``##### **NAME FEAT N**`` markup, leaving a bare bold
    ``**NAME**`` line above a class-trait line. These are recovered as level-less feats."""

    MD = ("##### **FIREBALL SPELL 3**\n**Range** 500 feet\nA blast of fire.\n\n"
          "**GREAT CLEAVE**\n**BARBARIAN** **RAGE**\n**Prerequisites** Cleave\n"
          "Your fury carries your weapon through multiple foes.\n")

    def setUp(self):
        self.boxes = {b.name: b for b in parse_stat_boxes(self.MD)}

    def test_type2_feat_recovered_with_unknown_level(self):
        gc = self.boxes.get("GREAT CLEAVE")
        self.assertIsNotNone(gc)
        self.assertEqual(gc.kind, "FEAT")
        self.assertEqual(gc.category, "feat")
        self.assertIsNone(gc.level)                       # no reliable level → None

    def test_type2_description_and_fields(self):
        gc = self.boxes["GREAT CLEAVE"]
        self.assertIn("Your fury carries your weapon", gc.description)
        self.assertNotIn("BARBARIAN", gc.description)     # trait line isn't prose
        self.assertEqual(dict(gc.fields).get("Prerequisites"), "Cleave")

    def test_type2_heading_bounds_the_preceding_box(self):
        # The recovered heading caps FIREBALL's body, so the feat's prose can't bleed in.
        self.assertNotIn("fury", self.boxes["FIREBALL"].description)

    def test_bold_trait_line_is_not_a_feat(self):
        # A bold ALL-CAPS line that is itself a trait must not become a feat box.
        boxes = parse_stat_boxes("**BARBARIAN**\n**BARBARIAN**\nRaging text here.\n")
        self.assertEqual(boxes, [])

    def test_bold_name_without_trait_anchor_is_ignored(self):
        # A bold ALL-CAPS phrase not followed by a class trait is not a feat.
        boxes = parse_stat_boxes("**A LOUD SHOUT**\nJust some emphasized prose, no trait.\n")
        self.assertEqual(boxes, [])


class TestMarkupTailTrim(unittest.TestCase):
    """A bled-in markdown table / heading / sidebar tail (starting with ``##``+ or
    ``~~``) is cut from a card description — it's never part of real prose."""

    def test_trims_level_header_tail(self):
        from lorehound.stat_boxes import _trim_markup_tail
        self.assertEqual(_trim_markup_tail("Real prose ends here. #### 5TH LEVEL"),
                         "Real prose ends here.")

    def test_trims_struck_table_and_subheader(self):
        from lorehound.stat_boxes import _trim_markup_tail
        self.assertEqual(
            _trim_markup_tail("You become trained. ##### Other Currency #### Large 12"),
            "You become trained.")
        self.assertEqual(_trim_markup_tail("Cast it. ~~Untrained~~ ~~10~~ ~~Trained~~"),
                         "Cast it.")

    def test_clean_prose_untouched(self):
        from lorehound.stat_boxes import _trim_markup_tail
        s = "You gain a +1 circumstance bonus to Reflex saves; nothing structural here."
        self.assertEqual(_trim_markup_tail(s), s)

    def test_end_to_end_table_bleed_removed(self):
        # The literal case: a currency table bled into WEAPON PROFICIENCY's body.
        md = ("##### **WEAPON PROFICIENCY FEAT 1**\n"
              "You become trained in all simple weapons. ##### Other Currency #### "
              "Price Large 12 Huge 24 Gargantuan 48\n")
        desc = parse_stat_boxes(md)[0].description
        self.assertEqual(desc, "You become trained in all simple weapons.")
