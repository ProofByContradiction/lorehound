"""Unit tests for the system-agnostic career model (/class) — no Discord/network.

Covers the T2K column-card detector (transpose, specialty sub-table, section
attribution, fragment rejection) and the generic search-assemble fallback.

Run with:  python -m unittest tests.test_careers
"""

import unittest

from lorehound.careers import (
    Career,
    _title,
    assemble_career,
    careers_from_card,
    detect_careers,
)
from lorehound.search_index import Chunk, SearchHit


def _card(rows, game="Twilight 2000 (4E)", source="Cards.pdf", locator="p. 2"):
    return Chunk(
        game=game, source=source, category="card", section="CAREER",
        locator=locator, text="", rows=rows,
    )


class TestColumnCardDetector(unittest.TestCase):
    # FIREMAN/EMT columns; EMT's STARTING GEAR value is blank on the label row so
    # the continuation row ("Medkit") must attach to Starting Gear, not Skills.
    CARD = [
        ["CAREER", "FIREMAN", "EMT"],
        ["REQUIREMENTS", "STR B+", "EMP B+"],
        ["SKILLS", "COMMAND, TECH", "MEDICAL AID, TECH"],
        ["STARTING GEAR", "Axe", ""],
        ["", "Helmet", "Medkit"],
        ["SPECIALITIES (D6)", "", ""],
        ["1", "Combat Medic", "Field Surgeon"],
        ["2", "Runner", "Racer"],
    ]

    def test_one_career_per_column_with_acronym(self):
        names = [c.name for c in careers_from_card(_card(self.CARD))]
        self.assertEqual(names, ["Fireman", "EMT"])  # EMT acronym preserved

    def test_fields_and_specialty_grid(self):
        fire = careers_from_card(_card(self.CARD))[0]
        d = {s.label: s for s in fire.sections}
        self.assertEqual(d["Requirements"].text, "STR B+")
        self.assertIn("COMMAND", d["Skills"].text)
        self.assertEqual(d["Starting Gear"].text, "Axe, Helmet")  # continuation folded in
        spec = next(s for s in fire.sections if s.rows)
        self.assertEqual(spec.rows[0], ["Roll (D6)", "Specialty"])
        self.assertIn(["1", "Combat Medic"], spec.rows)

    def test_blank_value_label_still_opens_section(self):
        # The regression: EMT's blank Starting Gear value must not let "Medkit"
        # leak into Skills.
        emt = careers_from_card(_card(self.CARD))[1]
        d = {s.label: s for s in emt.sections}
        self.assertEqual(d["Skills"].text, "MEDICAL AID, TECH")
        self.assertEqual(d["Starting Gear"].text, "Medkit")

    def test_rejects_roll_fragment_and_draft_card(self):
        frag = _card([["2", "Melee", "Racer"], ["3", "Cook", "Runner"]])
        self.assertEqual(careers_from_card(frag), [])  # numeric first cell = fragment
        draft = _card([["LAST CAREER", "MILITARY", "BLUE COLLAR"], ["x", "y", "z"]])
        self.assertEqual(careers_from_card(draft), [])  # draft mechanic, not careers


class TestSiblingSpecialtyMerge(unittest.TestCase):
    """T2K military careers: names+gear on one page, the SPECIALTY (D6) grid on the
    next (unnamed columns that position-align to the careers)."""

    def test_merges_specialty_grid_by_column(self):
        gear = _card(
            [["", "COMBAT ARMS", "COMBAT SUPPORT"], ["STARTING GEAR", "✓ Rifle", "✓ Radio"]],
            locator="p. 35",
        )
        spec = _card(
            [["SPECIALTY (D6)", "", ""], ["1", "Rifleman", "Intelligence"],
             ["2", "Redleg", "Linguist"]],
            locator="p. 34",
        )
        idx = detect_careers([gear, spec])["Twilight 2000 (4E)"]
        ca = idx["combat arms"]
        grid = next(s for s in ca.sections if s.rows)
        self.assertEqual(grid.label, "Specialities (D6)")
        self.assertIn(["1", "Rifleman"], grid.rows)        # column 1 = Combat Arms
        cs = idx["combat support"]
        grid2 = next(s for s in cs.sections if s.rows)
        self.assertIn(["1", "Intelligence"], grid2.rows)   # column 2 = Combat Support

    def test_no_merge_when_card_has_own_specialties(self):
        # A self-contained civilian card must not pick up a sibling grid.
        own = _card([["CAREER", "NURSE"], ["SPECIALITIES (D6)", ""], ["1", "Teacher"]])
        idx = detect_careers([own])["Twilight 2000 (4E)"]
        grids = [s for s in idx["nurse"].sections if s.rows]
        self.assertEqual(len(grids), 1)
        self.assertIn(["1", "Teacher"], grids[0].rows)


class TestSectionMerge(unittest.TestCase):
    """A career split across cards (T2K military: stats on one page, gear on the
    next) must merge into one complete card, not overwrite."""

    def test_same_named_cards_merge_sections(self):
        stats = _card(
            [["CAREER", "SCOUT"], ["REQUIREMENTS", "INT B+"], ["SKILLS", "Recon"]],
            locator="p. 34",
        )
        gear = _card(
            [["CAREER", "SCOUT"], ["STARTING GEAR", "✓ Rifle"]], locator="p. 35"
        )
        scout = detect_careers([stats, gear])["Twilight 2000 (4E)"]["scout"]
        have = {s.label for s in scout.sections}
        self.assertIn("Requirements", have)
        self.assertIn("Skills", have)
        self.assertIn("Starting Gear", have)  # merged from the second card


class TestColStarts(unittest.TestCase):
    def test_clusters_columns_by_gap(self):
        from lorehound.pdf_tables import _col_starts

        # label@95, then careers; wrapped words within a cell stay in their column.
        xs = [95, 161, 161, 239, 307, 335, 377, 378, 466]
        self.assertEqual(_col_starts(xs, gap=34), [95, 161, 239, 307, 377, 466])


class TestCareerGridFallback(unittest.TestCase):
    """The geometric reconstructor only runs where find_tables didn't already get
    a clean career card — otherwise it duplicates/mangles cleanly-detected pages."""

    def test_detects_clean_career_card(self):
        from lorehound.pdf_tables import _has_clean_career_card

        clean = [{"rows": [["CAREER", "FIREMAN", "EMT"], ["REQUIREMENTS", "STR B+", "EMP B+"],
                            ["SKILLS", "x", "y"]]}]
        self.assertTrue(_has_clean_career_card(clean))

    def test_fragments_are_not_a_clean_card(self):
        from lorehound.pdf_tables import _has_clean_career_card

        frags = [{"rows": [["1", "Melee", "Racer"], ["2", "Runner", "Sniper"]]},
                 {"rows": [["SPECIALTY (D6)", "", ""], ["1", "Rifleman", "Intel"]]}]
        self.assertFalse(_has_clean_career_card(frags))


class TestSourceProfiles(unittest.TestCase):
    """The hybrid indexer registry: known games get a profile, others fall back to
    the generic baseline (None)."""

    def test_t2k_profile_registered_and_matches(self):
        import lorehound.pdf_tables  # noqa: F401 - import registers the T2K profile
        from lorehound.sources import profile_for

        p = profile_for("Twilight 2000 (4E)")
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "Twilight 2000 (4E)")
        self.assertTrue(p.reconstructors)

    def test_unknown_game_has_no_profile(self):
        import lorehound.pdf_tables  # noqa: F401
        from lorehound.sources import profile_for

        self.assertIsNone(profile_for("Call of Cthulhu 7e"))

    def test_profile_matches_substring(self):
        from lorehound.sources import SourceProfile

        prof = SourceProfile("X", ("traveller",))
        self.assertTrue(prof.matches("Traveller (Mongoose)"))
        self.assertFalse(prof.matches("Twilight 2000"))


class TestTitle(unittest.TestCase):
    def test_words_acronyms_and_mixed(self):
        self.assertEqual(_title("FIREMAN"), "Fireman")
        self.assertEqual(_title("EMT"), "EMT")          # short acronym kept
        self.assertEqual(_title("COMBAT ARMS"), "Combat Arms")


class TestDetectCareers(unittest.TestCase):
    def test_groups_by_game(self):
        chunks = [
            _card([["CAREER", "SCOUT"], ["REQUIREMENTS", "INT B+"]], game="Game One"),
            _card([["CAREER", "PILOT"], ["SKILLS", "DRIVE"]], game="Game Two"),
        ]
        idx = detect_careers(chunks)
        self.assertIn("scout", idx["Game One"])
        self.assertIn("pilot", idx["Game Two"])


class TestAssemble(unittest.TestCase):
    """The generic fallback for systems without structured cards."""

    @staticmethod
    def _search_over(chunks):
        def search(query, game=None, top_k=5, **kw):
            return [SearchHit(chunk=c, score=float(10 - i)) for i, c in enumerate(chunks)]
        return search

    def test_assembles_when_name_appears(self):
        chunks = [
            Chunk("Trav", "Core", "rules", "Agent › Qualification", "p. 22",
                  "Agent qualification INT 6+; advancement and survival apply."),
        ]
        car = assemble_career(self._search_over(chunks), "Trav", "Agent")
        self.assertIsInstance(car, Career)
        self.assertTrue(car.assembled)
        self.assertEqual(car.name, "Agent")

    def test_none_when_name_absent(self):
        # Generic qualification prose that names no career is noise → no card.
        chunks = [
            Chunk("Trav", "Core", "rules", "Combat", "p. 80",
                  "Generic qualification prose mentioning no career name at all."),
        ]
        self.assertIsNone(assemble_career(self._search_over(chunks), "Trav", "Agent"))


if __name__ == "__main__":
    unittest.main()
