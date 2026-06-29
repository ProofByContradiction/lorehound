"""Tests for the extraction-quality eval (scripts/extraction_eval.py).

Two layers, mirroring tests/test_retrieval.py:

* ``TestExtractionScorer`` is pure-logic on synthetic tables and runs everywhere
  (CI): it pins the table-matching + structure-checking the eval depends on.
* ``TestExtractionGoldRegression`` runs the real gold set against the real cached
  tables. That needs a populated ``cache/`` (copyrighted books, not in the repo), so
  it's opt-in: set ``LOREHOUND_GOLD_EVAL=1``.

Run with:  python -m unittest tests.test_extraction
Live gold:  LOREHOUND_GOLD_EVAL=1 python -m unittest tests.test_extraction
"""

import os
import unittest

from lorehound.pdf_tables import (
    _dedupe_words,
    _frag_fraction,
    _recover_trailing_rows,
    _shaded_table_regions,
    _split_stacked_catalogs,
    _split_stacked_tables,
    _title,
    _unroll_repeated_columns,
)
from scripts.extraction_eval import _find_table, score_entry, summarize


class TestSplitStackedTables(unittest.TestCase):
    """_split_stacked_tables — separate two roll tables merged into one (the Aliens
    career Skills A/B and Mishaps/Events fix), without touching non-roll tables."""

    def test_splits_two_roll_tables_at_die_header(self):
        rows = [["1D", "Personal Development", "Service Skills"]] \
            + [[str(i), "a", "b"] for i in range(1, 7)] \
            + [["1D", "FIELD AGENT", "ADMIN"]] \
            + [[str(i), "c", "d"] for i in range(1, 7)]
        segs = _split_stacked_tables(rows)
        self.assertEqual([len(s) for s in segs], [7, 7])
        self.assertEqual(segs[1][0], ["1D", "FIELD AGENT", "ADMIN"])

    def test_trims_banner_between_mishaps_and_events(self):
        rows = [["1D", "MISHAP"]] + [[str(i), "x"] for i in range(1, 7)] \
            + [["", "EVENTS TABLE"], ["2D", "EVENT"]] + [[str(i), "y"] for i in range(2, 13)]
        segs = _split_stacked_tables(rows)
        self.assertEqual(len(segs), 2)
        self.assertEqual(len(segs[0]), 7)               # Mishaps: header + 1-6, banner trimmed
        self.assertEqual(segs[0][-1][0], "6")
        self.assertEqual(segs[1][0], ["2D", "EVENT"])   # Events starts at its die header

    def test_noop_on_non_roll_table(self):
        # A weapon/career card (header isn't a die label) must never be split.
        rows = [["Weapon", "Damage", "Bulk"], ["Longsword", "1d8 S", "1"],
                ["Maul", "1d12 B", "2"], ["Dagger", "1d4 P", "L"]]
        self.assertEqual(_split_stacked_tables(rows), [rows])

    def test_noop_on_single_roll_table(self):
        rows = [["1D", "Mishap"]] + [[str(i), "x"] for i in range(1, 7)]
        self.assertEqual(_split_stacked_tables(rows), [rows])


class TestSplitStackedCatalogs(unittest.TestCase):
    """_split_stacked_catalogs — split a catalog blob (the Pathfinder weapons page:
    Simple/Martial sub-tables under a repeated header) WITHOUT shredding career cards
    or stat blocks (which look header-ish per row but never repeat a signature)."""

    def test_splits_at_repeated_header_recovering_all_items(self):
        rows = [
            ["Simple Weapons", "Price", "Damage", "Bulk", "Hands", "Group"],
            ["Club", "0", "1d6 B", "1", "1", "Club"],
            ["Dagger", "2 sp", "1d4 P", "L", "1", "Knife"],
            ["TABLE 6–7: MELEE", "WEAPONS", "", "", "", ""],   # leaked caption → trimmed
            ["Martial Weapons", "Price", "Damage", "Bulk", "Hands", "Group"],
            ["Greataxe", "2 gp", "1d12 S", "2", "2", "Axe"],
            ["Greatsword", "2 gp", "1d12 S", "2", "2", "Sword"],
        ]
        segs = _split_stacked_catalogs(rows)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0], rows[0:3])                  # Simple: header + Club + Dagger
        self.assertEqual([r[0] for r in segs[1][1:]], ["Greataxe", "Greatsword"])
        self.assertNotIn("TABLE 6–7: MELEE", [r[0] for s in segs for r in s])

    def test_does_not_shred_a_career_card(self):
        # Header-ish rows (CAREER, REQUIREMENTS, SKILLS…) but no repeated signature.
        rows = [
            ["CAREER", "POLICE OFFICER", "DETECTIVE", "SWAT"],
            ["REQUIREMENTS", "No D attribute", "EMP B+", "AGL B+"],
            ["SKILLS", "Close Combat", "Recon", "Recon"],
            ["STARTING GEAR", "Pistol", "Lockpicks", "Rifle"],
            ["ROLL", "Scout", "Linguist", "Scout"],
            ["RANK", "Patrol", "Sergeant", "Officer"],
        ]
        self.assertEqual(_split_stacked_catalogs(rows), [rows])

    def test_does_not_shred_a_ship_statblock(self):
        # Component rows carry "—" placeholders / digits → never header-ish.
        rows = [
            ["Hull", "200 tons", "—", "12"],
            ["M-Drive", "Thrust 1", "2", "4"],
            ["Fuel Scoops", "—", "—", ""],
            ["Library", "—", "—", ""],
            ["Sensors", "Civilian", "1", "3"],
            ["Software", "Manoeuvre", "—", ""],
        ]
        self.assertEqual(_split_stacked_catalogs(rows), [rows])

    def test_single_catalog_one_header_unchanged(self):
        rows = [["Weapon", "Price", "Damage", "Bulk"]] + \
            [[w, "1 gp", "1d8", "1"] for w in ("Sword", "Axe", "Mace", "Spear", "Bow")]
        self.assertEqual(_split_stacked_catalogs(rows), [rows])


class TestDedupeAndUnroll(unittest.TestCase):
    """De-dup of double-struck words + unroll of side-by-side repeated columns —
    the Pathfinder ability-modifiers fix."""

    def test_dedupe_drops_same_position_duplicates(self):
        ws = [_w(10, 10, 20, 18, "1"), _w(10, 10, 20, 18, "1"), _w(40, 10, 50, 18, "-5")]
        self.assertEqual(len(_dedupe_words(ws)), 2)  # the duplicate "1" is dropped

    def test_dedupe_keeps_distinct_words(self):
        ws = [_w(10, 10, 20, 18, "1"), _w(10, 30, 20, 38, "1")]  # same text, different y
        self.assertEqual(len(_dedupe_words(ws)), 2)

    def test_unroll_stacks_repeated_groups(self):
        rows = [
            ["Ability Score", "Modifier", "Ability Score", "Modifier"],
            ["1", "-5", "14-15", "+2"],
            ["2-3", "-4", "16-17", "+3"],
        ]
        out = _unroll_repeated_columns(rows)
        self.assertEqual(out[0], ["Ability Score", "Modifier"])      # one header group
        self.assertIn(["14-15", "+2"], out)                          # right half stacked in
        self.assertIn(["1", "-5"], out)
        self.assertTrue(all(len(r) == 2 for r in out))

    def test_unroll_noop_on_normal_header(self):
        rows = [["Weapon", "Damage", "Bulk"], ["Longsword", "1d8 S", "1"]]
        self.assertEqual(_unroll_repeated_columns(rows), rows)


class TestFragFraction(unittest.TestCase):
    """_frag_fraction — the Stage B confidence metric that picks the less
    column-fragmented of two shaded-table reconstructions (lower = cleaner)."""

    def test_clean_table_scores_low(self):
        clean = [["Weapon", "Damage", "Bulk"], ["Longsword", "1d8 S", "1"], ["Maul", "1d12 B", "2"]]
        self.assertLess(_frag_fraction(clean), 0.15)

    def test_over_segmented_scores_higher(self):
        # column split mid-word leaves lowercase continuation fragments
        frag = [["Weapon", "Da", "mage", "Bu", "lk"], ["Longsword", "1d8", "atile", "1", "lk"]]
        self.assertGreater(_frag_fraction(frag), _frag_fraction(
            [["Weapon", "Damage", "Bulk"], ["Longsword", "1d8 S", "1"]]
        ))

    def test_empty_is_max(self):
        self.assertEqual(_frag_fraction([]), 1.0)


class _FakePage:
    """Minimal stand-in exposing get_drawings() for the shaded-band region test."""

    def __init__(self, rects):
        self._rects = rects

    def get_drawings(self):
        return [{"fill": (0, 0, 0), "rect": r} for r in self._rects]


class TestShadedTableRegions(unittest.TestCase):
    """_shaded_table_regions clusters the filled row-bands (Paizo-style shading)
    into table regions — the Stage-A ruling-independent detector's first stage."""

    def _rect(self, x0, y0, x1, y1):
        import fitz
        return fitz.Rect(x0, y0, x1, y1)

    def test_adjacent_wide_bands_form_one_region(self):
        bands = [self._rect(70, 640 + 24 * i, 520, 652 + 24 * i) for i in range(3)]
        regions = _shaded_table_regions(_FakePage(bands))
        self.assertEqual(len(regions), 1)
        self.assertLess(regions[0].y0, 640)   # padded up for a header row
        self.assertGreater(regions[0].y1, 700)

    def test_single_band_is_not_a_table(self):
        self.assertEqual(_shaded_table_regions(_FakePage([self._rect(70, 640, 520, 652)])), [])

    def test_narrow_bands_ignored(self):
        narrow = [self._rect(70, 640, 120, 652), self._rect(70, 664, 120, 676)]
        self.assertEqual(_shaded_table_regions(_FakePage(narrow)), [])


class _FakeTextPage:
    """Minimal stand-in exposing get_text('dict') for the card-title test.
    Each input line is (x0, y0, x1, y1, text, size); one line per block."""

    def __init__(self, lines):
        self._lines = lines

    def get_text(self, _kind):
        return {"blocks": [
            {"lines": [{"bbox": (x0, y0, x1, y1),
                        "spans": [{"text": text, "size": size}]}]}
            for (x0, y0, x1, y1, text, size) in self._lines
        ]}


class TestCardTitle(unittest.TestCase):
    """_title (#66) — name a stat card by the entry-NAME heading above its flavor
    paragraph, not by the nearest line (which is the flavor). Size-only logic."""

    def test_picks_name_above_flavor(self):
        # weapon card: NAME (size 10) over a long flavor paragraph (size 8 = body),
        # the flavor being the line nearest the table.
        page = _FakeTextPage([
            (100, 100, 160, 112, "M1911A1", 10.0),
            (100, 170, 400, 192, "A reliable .45 sidearm carried for decades. " * 2, 8.0),
        ])
        self.assertEqual(_title(page, 100, 200, 300, 280), "M1911A1")

    def test_real_heading_just_above_wins(self):
        # A genuine heading flush above the table is used directly.
        page = _FakeTextPage([
            (100, 50, 400, 60, "body text " * 6, 8.0),
            (100, 182, 200, 196, "MINES", 11.0),
        ])
        self.assertEqual(_title(page, 100, 200, 300, 280), "MINES")

    def test_column_overlap_keeps_cards_apart(self):
        # Two side-by-side cards: the right table must not steal the left name.
        page = _FakeTextPage([
            (70, 100, 120, 112, "M249", 10.0),                  # left column name
            (320, 100, 380, 112, "M240B", 10.0),               # right column name
            (300, 170, 520, 192, "A belt-fed machine gun. " * 2, 8.0),
        ])
        self.assertEqual(_title(page, 300, 200, 520, 280), "M240B")

    def test_running_title_in_top_margin_ignored(self):
        # A larger-than-body line in the page's top margin (y < 64) is the running
        # title, not an entry name — fall back to the nearest line instead.
        page = _FakeTextPage([
            (100, 40, 300, 58, "US MILITARY WEAPONS", 12.0),   # running header
            (100, 180, 400, 196, "the only line near the table", 8.0),
        ])
        self.assertEqual(_title(page, 100, 200, 300, 280), "the only line near the table")


# A faithful little table and a row-dropped copy of it (the D12-row failure mode).
_GOOD = {"page": 48, "title": "Chance of Success", "rows": [
    ["ATTRIBUTE/ SKILL", "D6", "D8", "D10", "D12"],
    ["–", "31%", "62%", "75%", "82%"],
    ["D6", "52%", "74%", "83%", "88%"],
    ["D8", "74%", "85%", "90%", "93%"],
    ["D10", "83%", "90%", "93%", "96%"],
    ["D12", "88%", "93%", "96%", "98%"],
]}
_DROPPED = {**_GOOD, "rows": _GOOD["rows"][:-1]}  # bottom D12 row missing

_ENTRY = {
    "id": "x", "system": "T", "page": 48, "match_cells": ["82%", "88%"],
    "expect_headers": ["D6", "D8", "D10", "D12"],
    "expect_row_labels": ["D6", "D8", "D10", "D12"], "min_rows": 6,
}


class TestExtractionScorer(unittest.TestCase):
    def test_find_table_by_match_cells(self):
        other = {"page": 9, "rows": [["x", "y"], ["1", "2"]]}
        self.assertIs(_find_table(_ENTRY, [other, _GOOD]), _GOOD)

    def test_find_table_prefers_stated_page(self):
        decoy = {"page": 5, "rows": _GOOD["rows"]}      # same cells, wrong page
        self.assertEqual(_find_table(_ENTRY, [decoy, _GOOD])["page"], 48)

    def test_complete_table_scores_correct(self):
        r = score_entry(_ENTRY, [_GOOD])
        self.assertTrue(r["correct"])
        self.assertEqual(r["missing_labels"], [])

    def test_dropped_row_is_flagged(self):
        r = score_entry(_ENTRY, [_DROPPED])
        self.assertFalse(r["correct"])
        self.assertEqual(r["missing_labels"], ["D12"])   # the exact dropped row
        self.assertFalse(r["rows_ok"])                   # 5 < 6

    def test_missing_table_is_not_found(self):
        r = score_entry(_ENTRY, [{"page": 1, "rows": [["a", "b"]]}])
        self.assertFalse(r["found"])
        self.assertFalse(r["correct"])

    def test_max_rows_flags_over_capture(self):
        # Two tables merged into one (more rows than expected) must fail, even though
        # all expected headers/labels are present and min_rows is satisfied.
        entry = {**_ENTRY, "max_rows": 6}
        over = {**_GOOD, "rows": _GOOD["rows"] + [["D14", "99%", "99%", "99%", "99%"]]}
        r = score_entry(entry, [over])
        self.assertFalse(r["correct"])
        self.assertTrue(r["too_many"])
        self.assertFalse(r["too_few"])

    def test_book_match_scopes_to_the_right_book(self):
        # Same cells + page in two books; book_match disambiguates (page collides
        # across books, so this is what prevents matching the wrong book's page).
        a = {**_GOOD, "_book": "TWILIGHT 2000 PLAYERS MANUAL"}
        b = {**_GOOD, "_book": "TRAVELLER CORE RULEBOOK 2022"}
        self.assertIs(_find_table({**_ENTRY, "book_match": "Traveller"}, [a, b]), b)
        self.assertIs(_find_table({**_ENTRY, "book_match": "Twilight"}, [a, b]), a)

    def test_summary_gates_on_non_advisory_only(self):
        results = [
            {"id": "a", "known_broken": True, "correct": False, "found": True},
            {"id": "b", "known_broken": False, "correct": True, "found": True},
        ]
        s = summarize(results)
        self.assertEqual(s["gate_accuracy"], 1.0)        # the advisory miss doesn't gate
        self.assertEqual(s["gated_entries"], 1)
        self.assertEqual(s["advisory_entries"], 1)


def _w(x0, y0, x1, y1, text):
    """A minimal PyMuPDF word tuple (x0, y0, x1, y1, text, block, line, word)."""
    return (x0, y0, x1, y1, text, 0, 0, 0)


class TestTrailingRowRecovery(unittest.TestCase):
    """``_recover_trailing_rows`` — the fix for the D12 bottom-row drop. Pins the
    behaviour on synthetic geometry (runs in CI): a clean column-aligned row flush
    below the grid is recovered; prose, gapped, or sparse content is not."""

    # 3 columns [100,150) [150,200) [200,250); two detected bands → pitch 12.
    XE = [100.0, 150.0, 200.0, 250.0]
    YE = [10.0, 22.0, 34.0]

    def _aligned_row(self, ycenter):
        h = 4.0
        return [
            _w(102, ycenter - h, 118, ycenter + h, "D12"),
            _w(155, ycenter - h, 175, ycenter + h, "88%"),
            _w(205, ycenter - h, 225, ycenter + h, "93%"),
        ]

    def test_recovers_flush_aligned_row(self):
        ye = _recover_trailing_rows(self._aligned_row(39), self.XE, list(self.YE), 1000)
        self.assertEqual(len(ye), len(self.YE) + 1)   # one row recovered
        self.assertGreater(ye[-1], self.YE[-1])

    def test_recovers_several_consecutive_rows(self):
        # Two rows spaced ~one pitch apart: each band lands on a single row.
        words = self._aligned_row(40) + self._aligned_row(58)
        ye = _recover_trailing_rows(words, self.XE, list(self.YE), 1000)
        self.assertEqual(len(ye), len(self.YE) + 2)

    def test_rejects_prose_straddling_a_column_edge(self):
        # A word crossing the 150 boundary is prose, not a grid cell → no recovery.
        words = [
            _w(102, 35, 118, 43, "When"),
            _w(140, 35, 168, 43, "pushing"),   # straddles x=150
            _w(205, 35, 225, 43, "reroll"),
        ]
        ye = _recover_trailing_rows(words, self.XE, list(self.YE), 1000)
        self.assertEqual(ye, self.YE)

    def test_ignores_content_past_one_pitch_gap(self):
        ye = _recover_trailing_rows(self._aligned_row(80), self.XE, list(self.YE), 1000)
        self.assertEqual(ye, self.YE)            # a blank-row gap stops recovery

    def test_requires_label_column_and_two_filled(self):
        only_interior = [_w(155, 35, 175, 43, "88%")]   # one non-label column
        self.assertEqual(
            _recover_trailing_rows(only_interior, self.XE, list(self.YE), 1000), self.YE
        )

    def test_stops_at_page_bottom(self):
        ye = _recover_trailing_rows(self._aligned_row(39), self.XE, list(self.YE), 30)
        self.assertEqual(ye, self.YE)            # recovered row would fall past page end


@unittest.skipUnless(
    os.environ.get("LOREHOUND_GOLD_EVAL"),
    "set LOREHOUND_GOLD_EVAL=1 to run the live extraction gold regression (needs cache/)",
)
class TestExtractionGoldRegression(unittest.TestCase):
    def test_known_good_tables_stay_correct(self):
        from scripts.extraction_eval import load_cache_tables, load_gold
        tables = load_cache_tables()
        if not tables:
            self.skipTest("empty cache")
        summary = summarize([score_entry(e, tables) for e in load_gold()])
        self.assertGreaterEqual(
            summary["gate_accuracy"], 0.80,
            msg=f"known-good table accuracy {summary['gate_accuracy']:.0%} regressed",
        )


if __name__ == "__main__":
    unittest.main()
