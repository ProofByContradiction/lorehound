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


class TestTravellerAnchors(unittest.TestCase):
    """Heading-anchored Traveller careers: each anchor owns the tables on its page
    range (up to the next career)."""

    def _anchor(self, name, page):
        return Chunk("Traveller (Mongoose)", "Core", "card", name, f"p. {page}", "",
                     rows=[["CAREER", name], ["PAGE", str(page)]])

    def _table(self, section, page):
        return Chunk("Traveller (Mongoose)", "Core", "tables", section, f"p. {page}",
                     "skills", rows=[["1D", "Skill"], ["1", "Gun Combat"], ["2", "Recon"]])

    def test_anchor_recognized(self):
        from lorehound.careers import _is_traveller_anchor

        self.assertTrue(_is_traveller_anchor(self._anchor("Agent", 23)))
        self.assertFalse(_is_traveller_anchor(self._table("pdf › 1D", 23)))

    def test_career_owns_its_page_range_tables(self):
        chunks = [
            self._anchor("Agent", 23), self._anchor("Army", 25),
            self._table("pdf › Service Skills", 23),  # Agent's page
            self._table("pdf › Personal Dev", 25),    # Army's page
        ]
        trav = detect_careers(chunks)["Traveller (Mongoose)"]
        self.assertIn("agent", trav)
        self.assertIn("army", trav)
        agent_pages = {s.label for s in trav["agent"].sections}
        self.assertTrue(any("p.23" in p for p in agent_pages))   # got p.23
        self.assertFalse(any("p.25" in p for p in agent_pages))  # not Army's p.25


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


def _word(x0, y, text, *, width=10.0):
    """A minimal PyMuPDF word tuple (x0, y0, x1, y1, text, block, line, word)."""
    return (x0, y, x0 + width, y + 9.0, text, 0, 0, 0)


class TestTravellerColumnAnchors(unittest.TestCase):
    """The geometric column clusterer used to rebuild Mongoose Traveller career
    sub-tables from word x-positions (where find_tables drops columns)."""

    def test_roll_index_is_its_own_column_even_when_close(self):
        # The roll index sits ~20pt left of column 1 (Navy/Army), closer than the
        # gap — it must still be its own column.
        from lorehound.pdf_tables import _trav_col_anchors

        words = []
        for i, y in enumerate((10, 22, 34)):
            words += [_word(85, y, str(i + 1)), _word(106, y, "STR"), _word(225, y, "Pilot"),
                      _word(315, y, "Medic")]
        cols = _trav_col_anchors(words, gap=30.0, numeric_only=True)
        self.assertEqual(len(cols), 4)  # roll + 3 data columns, none merged

    def test_wrapped_continuation_does_not_mint_a_column(self):
        # A single wrapped cell ("Electronics (computers)") shouldn't add a column.
        from lorehound.pdf_tables import _trav_col_anchors

        words = []
        for i, y in enumerate((10, 22, 34, 46)):
            words += [_word(85, y, str(i + 1)), _word(126, y, "Recon"), _word(270, y, "Stealth"),
                      _word(400, y, "Carouse")]
        words += [_word(452, 22, "(computers)")]  # one stray continuation in row 2
        cols = _trav_col_anchors(words, gap=30.0, numeric_only=True)
        self.assertEqual(len(cols), 4)


class TestTravellerSkillsSplit(unittest.TestCase):
    """The Skills band splits into Table A (universal skills, header forced to the
    fixed Mongoose names) and Table B (specialist skills, header kept from text)."""

    def _skills_words(self):
        words = []
        # Table A header (present in PDF text as an uppercase 1D-led row).
        words += [_word(85, 0, "1D"), _word(126, 0, "PERSONAL"), _word(160, 0, "DEVELOPMENT"),
                  _word(270, 0, "SERVICE"), _word(300, 0, "SKILLS"),
                  _word(400, 0, "ADVANCED"), _word(440, 0, "EDUCATION")]
        for i, y in enumerate((12, 24, 36, 48, 60, 72), start=1):
            words += [_word(85, y, str(i)), _word(126, y, "Gun"), _word(270, y, "Drive"),
                      _word(400, y, "Medic")]
        # Table B header (specialist assignments — uppercase, kept verbatim).
        words += [_word(85, 90, "1D"), _word(126, 90, "LAW"), _word(160, 90, "ENFORCEMENT"),
                  _word(270, 90, "INTELLIGENCE"), _word(400, 90, "CORPORATE")]
        for i, y in enumerate((102, 114, 126), start=1):
            words += [_word(85, y, str(i)), _word(126, y, "Recon"), _word(270, y, "Stealth"),
                      _word(400, y, "Carouse")]
        return words

    def test_table_a_gets_fixed_header_and_b_is_split_off(self):
        from lorehound.pdf_tables import _trav_skills_sections

        secs = _trav_skills_sections(self._skills_words(), 5, -10, 140)
        titles = [s["title"] for s in secs]
        self.assertEqual(titles, ["Skills and training", "Specialist Skills"])
        a, b = secs[0]["rows"], secs[1]["rows"]
        self.assertEqual(
            a[0], ["Roll", "Personal Development", "Service Skills", "Advanced Education"]
        )
        self.assertEqual(len(a), 7)  # header + 6 roll rows
        # Table B keeps its uppercase assignment header, but with a "Roll" first col.
        self.assertEqual(b[0], ["Roll", "LAW ENFORCEMENT", "INTELLIGENCE", "CORPORATE"])
        self.assertEqual(len(b), 4)  # header + 3 roll rows


class TestCareerSectionLabel(unittest.TestCase):
    """The clean reconstructor titles map to tidy /class card labels."""

    def test_maps_reconstructor_titles(self):
        from lorehound.careers import _career_section_label

        self.assertEqual(_career_section_label("Skills and training"), "Skills")
        self.assertEqual(_career_section_label("Specialist Skills"), "Specialist Skills")
        self.assertEqual(_career_section_label("Ranks and bonuses"), "Ranks")
        self.assertEqual(_career_section_label("Career progress"), "Career Progress")
        self.assertEqual(_career_section_label("Mustering out benefits"), "Mustering Out")
        self.assertEqual(_career_section_label("Mishaps"), "Mishaps")
        self.assertEqual(_career_section_label("Events"), "Events")
        self.assertEqual(_career_section_label("1D"), "Skills")  # bare-header fragment


class TestCareerAnchorGuard(unittest.TestCase):
    """drive_client keeps the anchor card but drops the mangled section tables when
    swapping in the reconstructed ones."""

    def test_recognizes_anchor_only(self):
        from lorehound.drive_client import _is_career_anchor

        self.assertTrue(_is_career_anchor([["CAREER", "Agent"], ["PAGE", "23"]]))
        self.assertFalse(_is_career_anchor([["1D", "Skill"], ["1", "Gun Combat"]]))
        self.assertFalse(_is_career_anchor([["Roll", "Personal Development"]]))


class _FakePage:
    """Minimal stand-in for a fitz Page that counts get_text() calls, so the
    per-page memoization can be asserted without a real PDF."""

    def __init__(self, dict_val=None, words_val=None):
        self._dict = {"blocks": []} if dict_val is None else dict_val
        self._words = [] if words_val is None else words_val
        self.calls = {"dict": 0, "words": 0}

    def get_text(self, kind="text"):
        if kind == "dict":
            self.calls["dict"] += 1
            return self._dict
        if kind == "words":
            self.calls["words"] += 1
            return self._words
        return ""


class TestPageTextMemo(unittest.TestCase):
    """Per-page get_text caching that makes the Traveller career scan cheap (it
    runs the heading scan on every page, then re-scans career pages)."""

    def test_dict_and_words_parsed_once_per_page(self):
        from lorehound.pdf_tables import _page_dict, _page_words

        p = _FakePage(dict_val={"blocks": [1]}, words_val=[("w",)])
        self.assertIs(_page_dict(p), _page_dict(p))  # same object, served from cache
        self.assertEqual(p.calls["dict"], 1)
        _page_words(p)
        _page_words(p)
        self.assertEqual(p.calls["words"], 1)


class TestTravListTransforms(unittest.TestCase):
    """Pure grid transforms used by every section reconstructor."""

    def test_drop_empty_cols(self):
        from lorehound.pdf_tables import _trav_drop_empty_cols

        self.assertEqual(
            _trav_drop_empty_cols([["A", "", "B"], ["1", "", "2"]]),
            [["A", "B"], ["1", "2"]],
        )

    def test_merge_headerless_spill_into_left(self):
        from lorehound.pdf_tables import _trav_merge_headerless_cols

        # The 3rd column has a blank header -> a wrapped-cell spill; fold it left.
        rows = [["Roll", "Skill", ""], ["1", "Melee", "(unarmed)"], ["2", "Drive", ""]]
        self.assertEqual(
            _trav_merge_headerless_cols(rows),
            [["Roll", "Skill"], ["1", "Melee (unarmed)"], ["2", "Drive"]],
        )


class TestTravSectionReconstructors(unittest.TestCase):
    """The remaining geometric career-section reconstructors (Ranks, Mustering)."""

    def test_ranks_dedups_repeated_header(self):
        from lorehound.pdf_tables import _trav_ranks_section

        words = [_word(85, 0, "RANK"), _word(300, 0, "SKILL"),
                 _word(85, 14, "RANK"), _word(300, 14, "SKILL")]  # uppercase shadow duplicate
        for i, y in enumerate((28, 42, 56)):
            words += [_word(85, y, str(i)), _word(300, y, "Admin")]
        secs = _trav_ranks_section(words, 5, -10, 100)
        self.assertEqual(len(secs), 1)
        self.assertEqual(secs[0]["title"], "Ranks and bonuses")
        rank_hdrs = [r for r in secs[0]["rows"] if r[0].strip().upper().startswith("RANK")]
        self.assertEqual(len(rank_hdrs), 1)  # the shadow header row was deduped

    def test_mustering_reconstructs_cash_benefits(self):
        from lorehound.pdf_tables import _trav_mustering_section

        words = [_word(315, 0, "1D"), _word(385, 0, "CASH"), _word(455, 0, "BENEFITS")]
        for i, y in enumerate((14, 28, 42), start=1):
            words += [_word(315, y, str(i)), _word(385, y, "Cr1000"), _word(455, y, "Weapon")]
        secs = _trav_mustering_section(words, 5, -10, 100)
        self.assertEqual(len(secs), 1)
        self.assertEqual(secs[0]["title"], "Mustering out benefits")
        self.assertGreaterEqual(len(secs[0]["rows"]), 4)  # header + 3 benefit rows


if __name__ == "__main__":
    unittest.main()
