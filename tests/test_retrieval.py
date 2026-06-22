"""Retrieval regression tests — guard the ranking/scoping logic the gold eval
relies on, plus a gated end-to-end gold-query regression.

Two layers:

* ``TestRetrievalInvariants`` / ``TestGoldMatching`` are pure-logic and run
  everywhere (including CI): they pin the BM25 scoping + heading-boost behaviour
  and the gold eval's fuzzy matcher, on synthetic data.
* ``TestGoldRegression`` runs the *real* gold set against the *real* indexed
  library. That needs Google Drive configured + a populated ``cache/`` (the
  copyrighted books can't live in the repo/CI), so it is opt-in: set
  ``LOREHOUND_GOLD_EVAL=1``. See ``scripts/retrieval_eval.py``.

Run with:  python -m unittest tests.test_retrieval
Full gold regression:  LOREHOUND_GOLD_EVAL=1 python -m unittest tests.test_retrieval
"""

import os
import unittest

from lorehound.search_index import Chunk, SearchIndex
from scripts.retrieval_eval import _norm, fact_present, resolve_game

# In-suite regression floor for the live gold eval. The measured baseline was
# 0.32 fact-recall (2026-06-22); this floor is set below it so the test fails
# only on a genuine retrieval regression, not on routine tuning noise.
_REGRESSION_FLOOR = 0.25


def _toks(s: str) -> set:
    return set(s.lower().split())


class TestRetrievalInvariants(unittest.TestCase):
    """Scoping + ranking invariants the gold eval depends on, on synthetic chunks."""

    def _index(self) -> SearchIndex:
        chunks = [
            Chunk(
                "Traveller (Mongoose)", "Core", "rules", "Skill Checks", "p. 59",
                "To make a skill check roll 2D6, add your skill level and the "
                "characteristic DM; 8 or more succeeds at an Average task.",
            ),
            Chunk(
                "Traveller (Mongoose)", "Core", "rules", "Character Creation", "p. 31",
                "During creation you choose skills; a skill check may come up later.",
            ),
            Chunk(
                "Twilight 2000 (4E)", "Core", "rules", "Skill Checks", "p. 40",
                "Roll the attribute die and the skill die; each die showing 6 or "
                "more is a success.",
            ),
            Chunk(
                "Twilight 2000 (4E)", "Core", "items", "US Weapons › M16", "p. 103",
                "The M16 assault rifle fires 5.56mm rounds with a rate of fire of 5.",
            ),
        ]
        idx = SearchIndex()
        idx.build(chunks)
        return idx

    def test_game_scoping_isolates_systems(self):
        hits = self._index().search("skill check", game="Twilight 2000 (4E)")
        self.assertTrue(hits)
        self.assertTrue(all(h.chunk.game == "Twilight 2000 (4E)" for h in hits))

    def test_defining_section_outranks_passing_mention(self):
        hits = self._index().search("skill check", game="Traveller (Mongoose)")
        self.assertEqual(hits[0].chunk.section, "Skill Checks")

    def test_category_scoping_filters(self):
        hits = self._index().search("M16 rifle", category="items")
        self.assertTrue(hits)
        self.assertTrue(all(h.chunk.category == "items" for h in hits))
        self.assertIn("M16", hits[0].chunk.section)

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self._index().search("   "), [])


class TestGoldMatching(unittest.TestCase):
    """The gold eval's game-resolution + fuzzy fact matcher (pure functions)."""

    def test_resolve_game_maps_label_to_folder(self):
        games = ["Traveller (Mongoose)", "Twilight 2000 (4E)"]
        self.assertEqual(resolve_game("Twilight 2000 (4E)", games), "Twilight 2000 (4E)")
        # Tolerant of edition annotations differing from the Drive folder name.
        self.assertEqual(resolve_game("Traveller (Mongoose)", ["Traveller", "T2K"]), "Traveller")

    def test_resolve_game_none_when_no_overlap(self):
        self.assertIsNone(resolve_game("Call of Cthulhu", ["Traveller", "T2K"]))

    def test_fact_present_substring(self):
        hay = "to make a skill check roll 2d6 and add the characteristic dm, 8+ succeeds"
        ok, cover = fact_present("roll 2D6", _norm(hay), _toks(_norm(hay)))
        self.assertTrue(ok)
        self.assertEqual(cover, 1.0)  # whole normalized fact is a substring

    def test_fact_present_is_inflection_tolerant(self):
        # 'success' should match 'successes' (shared 4+ char prefix), not require exact.
        hay = "each die showing 6 or more counts among your successes"
        ok, _ = fact_present("each die 6 success", _norm(hay), _toks(_norm(hay)))
        self.assertTrue(ok)

    def test_fact_absent_scores_low(self):
        hay = "this passage is about ship maintenance costs and fuel"
        ok, cover = fact_present("unskilled untrained penalty -3 DM", _norm(hay), _toks(_norm(hay)))
        self.assertFalse(ok)
        self.assertLess(cover, 0.5)


class TestLookupBadges(unittest.TestCase):
    """The /lookup type-badge mapping + reference-exclusion (cog-level logic)."""

    def test_badge_per_category(self):
        from lorehound.cogs import rules_cog as rc

        self.assertEqual(rc._badge("rules"), "📖")
        self.assertEqual(rc._badge("items"), "🎒")
        self.assertEqual(rc._badge("transport"), "🚙")
        self.assertEqual(rc._badge("tables"), "📊")
        self.assertEqual(rc._badge("card"), "🪖")
        self.assertEqual(rc._badge("anything-unknown"), "📖")  # safe default

    def test_lookup_excludes_reference_index(self):
        from lorehound.cogs import rules_cog as rc

        # /lookup surfaces every player-facing category but never the book's
        # alphabetical index / page-footer fragments.
        self.assertIn("reference", rc._LOOKUP_SKIP)
        self.assertNotIn("card", rc._LOOKUP_SKIP)
        self.assertNotIn("rules", rc._LOOKUP_SKIP)


@unittest.skipUnless(
    os.environ.get("LOREHOUND_GOLD_EVAL"),
    "set LOREHOUND_GOLD_EVAL=1 to run the live gold retrieval regression (needs Drive + cache)",
)
class TestGoldRegression(unittest.TestCase):
    """End-to-end: run the gold set against the real library; fail on a regression."""

    def test_fact_recall_above_floor(self):
        from scripts.retrieval_eval import build_service, load_gold, run_eval, summarize

        service = build_service()
        if service is None:
            self.skipTest("rules library not available (Drive not configured / offline / empty cache)")
        summary = summarize(run_eval(service, load_gold()))
        self.assertGreaterEqual(
            summary["gate_recall"],
            _REGRESSION_FLOOR,
            msg=(
                f"gold fact-recall {summary['gate_recall']:.0%} dropped below the "
                f"regression floor {_REGRESSION_FLOOR:.0%} — retrieval quality regressed."
            ),
        )


if __name__ == "__main__":
    unittest.main()
