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

from scripts.extraction_eval import _find_table, score_entry, summarize

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

    def test_summary_gates_on_non_advisory_only(self):
        results = [
            {"id": "a", "known_broken": True, "correct": False, "found": True},
            {"id": "b", "known_broken": False, "correct": True, "found": True},
        ]
        s = summarize(results)
        self.assertEqual(s["gate_accuracy"], 1.0)        # the advisory miss doesn't gate
        self.assertEqual(s["gated_entries"], 1)
        self.assertEqual(s["advisory_entries"], 1)


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
