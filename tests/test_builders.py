"""Tests for the equipment builders' data layer.

``TestArmorSuits`` is pure-logic on synthetic tables (runs in CI): it pins the parse
of the Traveller powered-armour catalogue — grade explosion, column-by-name, and the
grade-indexing of protection cells GradeSplit leaves merged. ``TestArmorLiveCache`` runs
the parser against the real cached CSC table; it needs a populated ``cache/`` (the
copyrighted book isn't in the repo), so it's opt-in via ``LOREHOUND_GOLD_EVAL=1``.
"""

import os
import unittest

from lorehound.builders.armor import (
    ArmorData,
    ArmorSuit,
    armor_flow,
    build_armor_data,
    suits_from_rows,
)
from lorehound.builders.model import SuitBuild
from lorehound.builders.render import build_summary, built_suit_sheet
from lorehound.chargen.engine import FAITHFUL, ChargenSession
from lorehound.sources import GradeSplit

# The Traveller profile's real grade-split config (detect PROTECTION+TL, count on TL).
_GS = GradeSplit(detect=("PROTECTION", "TL"), count_label="TL")

_HEADER = ["ARMOUR TYPE", "PROTECTION", "TL", "RAD", "STR", "DEX", "SLOTS", "KG", "COST", "REQUIRED SKILL"]
# Crammed rows exactly as the CSC p40 table extracts: one row per family, grades stacked.
_PLAIN = [
    "Battle Dress, Basic Battle Dress, Improved Battle Dress, Advanced",
    "+22 +25 +28", "13 14 15", "245 290 320", "+4 +6 +6", "+4 +4 +4",
    "16 16 18", "100 100 100", "Cr200000 Cr220000 Cr440000", "Vacc Suit 2 Vacc Suit 1 Vacc Suit 1",
]
_CERAMIC = [
    "Ceramic Battle Dress, Basic Ceramic Battle Dress, Improved Ceramic Battle Dress, Advanced",
    "+22 (+32 vs. fire, lasers, and energy) +25 (+35 vs. fire, lasers, and energy) "
    "+28 (+38 vs. fire, lasers, and energy)",
    "13 14 15", "245 290 320", "+4 +6 +6", "+4 +4 +4",
    "16 16 18", "100 100 100", "Cr400000 Cr440000 Cr880000", "Vacc Suit 2 Vacc Suit 1 Vacc Suit 1",
]


class TestArmorSuits(unittest.TestCase):
    def _parse(self, *rows):
        return suits_from_rows([_HEADER, *rows], grade_split=_GS)

    def test_explodes_grades_into_per_suit_records(self):
        suits = self._parse(_PLAIN)
        self.assertEqual([s.display for s in suits],
                         ["Battle Dress (Basic)", "Battle Dress (Improved)", "Battle Dress (Advanced)"])

    def test_pulls_clean_stats_by_column_name(self):
        basic = self._parse(_PLAIN)[0]
        self.assertEqual(basic,
                         ArmorSuit(name="Battle Dress", grade="Basic", protection="+22",
                                   tl="13", str_mod="+4", dex_mod="+4", slots=16, cost="Cr200000"))

    def test_slots_capacity_is_per_grade(self):
        self.assertEqual([s.slots for s in self._parse(_PLAIN)], [16, 16, 18])

    def test_grade_indexes_a_merged_protection_note(self):
        # GradeSplit leaves the note-carrying protection merged across grades; the parser
        # hands each grade its own value.
        cer = self._parse(_CERAMIC)
        self.assertEqual([s.protection for s in cer], [
            "+22 (+32 vs. fire, lasers, and energy)",
            "+25 (+35 vs. fire, lasers, and energy)",
            "+28 (+38 vs. fire, lasers, and energy)",
        ])

    def test_half_psi_note_is_kept_whole(self):
        psi = suits_from_rows([_HEADER, [
            "Psi-Commando Battle Dress, Basic Psi-Commando Battle Dress, Advanced",
            "+26 (+ ½ PSI) +29 (+ ½ PSI)", "15 16", "300 330", "+6 +6", "+4 +4",
            "20 22", "110 110", "MCr1.2 MCr2.4", "Vacc Suit 1 Vacc Suit 1",
        ]], grade_split=_GS)
        self.assertEqual([s.protection for s in psi], ["+26 (+ ½ PSI)", "+29 (+ ½ PSI)"])

    def test_columns_located_by_name_not_position(self):
        # A differently-ordered printing still parses (SLOTS moved before PROTECTION).
        header = ["ARMOUR TYPE", "SLOTS", "PROTECTION", "TL", "STR", "DEX", "COST"]
        row = ["Battle Dress, Basic Battle Dress, Improved",
               "16 18", "+22 +25", "13 14", "+4 +6", "+4 +4", "Cr200000 Cr220000"]
        suits = suits_from_rows([header, row], grade_split=_GS)
        self.assertEqual([(s.grade, s.slots, s.protection) for s in suits],
                         [("Basic", 16, "+22"), ("Improved", 18, "+25")])

    def test_non_armour_table_yields_nothing(self):
        rows = [["Weapon", "Damage"], ["Sword", "1d8"]]
        self.assertEqual(suits_from_rows(rows, grade_split=_GS), [])

    def test_empty_input(self):
        self.assertEqual(suits_from_rows([], grade_split=_GS), [])
        self.assertEqual(suits_from_rows([_HEADER], grade_split=_GS), [])


@unittest.skipUnless(
    os.environ.get("LOREHOUND_GOLD_EVAL"),
    "set LOREHOUND_GOLD_EVAL=1 to parse the real cached CSC armour table (needs cache/)",
)
class TestArmorLiveCache(unittest.TestCase):
    def test_parses_the_csc_powered_armour_table(self):
        import glob
        import json

        from lorehound import pdf_tables, sources  # noqa: F401 — pdf_tables registers the profile

        p40 = None
        for path in glob.glob("cache/*.json"):
            for t in json.load(open(path)).get("tables") or []:
                cells = {(c or "").strip().upper() for r in (t.get("rows") or []) for c in r}
                if t.get("page") == 40 and {"STR", "DEX", "SLOTS"} <= cells and "ARMOUR TYPE" in cells:
                    p40 = t
        if p40 is None:
            self.skipTest("CSC powered-armour table not in cache")
        gs = sources.profile_for("Traveller (Mongoose)").grade_split
        suits = suits_from_rows(p40["rows"], grade_split=gs)
        self.assertGreaterEqual(len(suits), 20)
        self.assertTrue(all(s.slots > 0 and s.cost and s.protection for s in suits))
        bd = [s for s in suits if s.name == "Battle Dress"]
        self.assertEqual({s.grade for s in bd}, {"Basic", "Improved", "Advanced"})


_SUITS = [
    ArmorSuit("Battle Dress", "Basic", "+22", "13", "+4", "+4", 16, "Cr200000"),
    ArmorSuit("Battle Dress", "Advanced", "+28", "15", "+6", "+4", 18, "Cr440000"),
    ArmorSuit("Scout Battle Dress", "", "+20", "13", "+2", "+6", 12, "Cr270000"),
]


class TestArmorData(unittest.TestCase):
    def test_families_are_unique_in_order(self):
        data = ArmorData(game="T", suits=_SUITS)
        self.assertEqual(data.families, ["Battle Dress", "Scout Battle Dress"])

    def test_grades_of_a_family(self):
        data = ArmorData(game="T", suits=_SUITS)
        self.assertEqual([s.grade for s in data.grades_of("Battle Dress")], ["Basic", "Advanced"])

    def test_build_data_finds_the_powered_armour_table(self):
        from lorehound import pdf_tables  # noqa: F401 — registers the Traveller profile

        class _Chunk:
            def __init__(self, game, rows):
                self.game, self.rows, self.source, self.locator = game, rows, "CSC", "p. 40"

        class _Rules:
            def __init__(self, chunks):
                self.index = type("Idx", (), {"chunks": chunks})()

        rules = _Rules([_Chunk("Traveller (Mongoose)", [_HEADER, _PLAIN])])
        data = build_armor_data(rules, "Traveller (Mongoose)")
        self.assertEqual(len(data.suits), 3)                       # 3 grades from one crammed row
        self.assertEqual(data.families, ["Battle Dress"])
        self.assertEqual(data.source, "CSC · p. 40")

    def test_build_data_ignores_other_games(self):
        from lorehound import pdf_tables  # noqa: F401

        class _Chunk:
            def __init__(self, game, rows):
                self.game, self.rows, self.source, self.locator = game, rows, "CSC", "p. 40"

        rules = type("R", (), {"index": type("Idx", (), {
            "chunks": [_Chunk("Some Other Game", [_HEADER, _PLAIN])]})()})()
        self.assertEqual(build_armor_data(rules, "Traveller (Mongoose)").suits, [])


class TestArmorFlow(unittest.TestCase):
    def _session(self, suits=_SUITS):
        data = ArmorData(game="T", suits=suits, source="CSC · p. 40")
        return ChargenSession(armor_flow, mode=FAITHFUL, draft=SuitBuild(game="T"),
                              data=data, draft_factory=lambda: SuitBuild(game="T"))

    def test_pick_family_then_grade_completes_the_build(self):
        s = self._session()
        self.assertEqual(s.current.id, "family")
        s.resolve("Battle Dress")
        self.assertEqual(s.current.id, "grade")
        s.resolve("Advanced")
        self.assertTrue(s.complete)
        d = s.draft
        self.assertEqual(
            (d.base, d.grade, d.protection, d.slots_total, d.cost),
            ("Battle Dress", "Advanced", "+28", 18, "Cr440000"),
        )

    def test_single_grade_family_skips_the_grade_step(self):
        s = self._session()
        s.resolve("Scout Battle Dress")            # only one grade → flow finishes
        self.assertTrue(s.complete)
        self.assertEqual((s.draft.base, s.draft.slots_total), ("Scout Battle Dress", 12))

    def test_back_returns_to_the_family_step(self):
        s = self._session()
        s.resolve("Battle Dress")
        self.assertTrue(s.can_back)
        s.back()
        self.assertEqual(s.current.id, "family")   # rebuilt onto a fresh SuitBuild draft

    def test_empty_catalogue_completes_without_a_suit(self):
        s = ChargenSession(armor_flow, mode=FAITHFUL, draft=SuitBuild(game="T"),
                           data=ArmorData(game="T", suits=[]),
                           draft_factory=lambda: SuitBuild(game="T"))
        self.assertTrue(s.complete)
        self.assertEqual(s.draft.base, "")


class TestBuiltSuitRender(unittest.TestCase):
    def _built(self):
        return SuitBuild(game="Traveller", base="Battle Dress", grade="Advanced",
                         protection="+28", str_mod="+6", dex_mod="+4", tl="15",
                         cost="Cr440000", slots_total=18, source="CSC · p. 40")

    def test_sheet_shows_headline_stats_and_slot_budget(self):
        out = built_suit_sheet(self._built())
        for token in ("Battle Dress (Advanced)", "+28", "0 / 18", "18 free", "Cr440000", "CSC · p. 40"):
            self.assertIn(token, out)

    def test_summary_empty_until_a_base_is_chosen(self):
        self.assertEqual(build_summary(SuitBuild(game="T")), "")
        self.assertIn("Battle Dress", build_summary(self._built()))


if __name__ == "__main__":
    unittest.main()
