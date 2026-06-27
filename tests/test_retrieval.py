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

# In-suite regression floor for the live gold eval. After the retrieval overhaul
# (stemming + worked-example rescue), gold-set calibration, and tuning HEADING_BOOST
# 2.0→1.0, gate fact-recall is ~0.88 (2026-06-27, top-8). This floor sits below that
# with headroom for newly-added (initially failing) targets — roughly one new hard
# target costs ~0.08 — so the test fails only on a genuine regression.
_REGRESSION_FLOOR = 0.75


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

    def test_heading_boost_does_not_bury_body_relevance(self):
        # A chunk that only matches via its breadcrumb must not outrank one densely
        # about the query in its body — the failure mode that an over-high HEADING_BOOST
        # caused (granular facts crowded out of the top-k). Guards the 2.0→1.0 tuning.
        idx = SearchIndex()
        idx.build([
            Chunk("T2K", "Core", "rules", "Combat › Ammo Dice › Overwatch", "p. 1",
                  "During overwatch you interrupt an enemy when they move into the open ground."),
            Chunk("T2K", "Core", "items", "Combat › Ammunition", "p. 2",
                  "Ammo dice are D6 rolled with a ranged attack; each ammo die showing a 6 is "
                  "an extra hit, and ammo dice are re-rolled when you push your roll."),
        ])
        hits = idx.search("ammo dice extra hit", game="T2K")
        self.assertIn("Ammunition", hits[0].chunk.section)

    def test_rel_cutoff_trims_weak_tail(self):
        idx = SearchIndex()
        idx.build([
            Chunk("G", "C", "rules", "Strong", "p. 1", "alpha alpha alpha alpha beta gamma"),
            Chunk("G", "C", "rules", "Weak", "p. 2", "alpha gamma delta epsilon zeta eta theta"),
        ])
        self.assertEqual(len(idx.search("alpha", game="G", min_rel=0.0)), 2)  # both kept
        strict = idx.search("alpha", game="G", min_rel=0.9)                   # trims the tail
        self.assertEqual([h.chunk.section for h in strict], ["Strong"])


class TestRetrievalOverhaul(unittest.TestCase):
    """Guards the retrieval overhaul on synthetic data (runs in CI): vocabulary
    normalization (stemming/equivalence) and worked-example breadcrumb rescue."""

    def test_stemming_bridges_word_forms(self):
        idx = SearchIndex()
        idx.build([
            Chunk("T2K", "Core", "rules", "Player Characters › Aging", "p. 41",
                  "From the second term roll a D8 for age effects; under your terms you lose a step."),
            Chunk("T2K", "Core", "rules", "Combat › Cover", "p. 70",
                  "Armoured vehicles give cover; modifiers apply to the roll."),
        ])
        # aging↔age (irregular equivalence), armor↔armour (spelling), plural folding.
        self.assertIn("Aging", idx.search("aging", game="T2K")[0].chunk.section)
        self.assertTrue(idx.search("armor", game="T2K"))            # finds "Armoured"
        self.assertTrue(idx.search("modifier", game="T2K"))         # finds "modifiers"
        self.assertTrue(idx.search("rolls", game="T2K"))            # plural folds to "roll"

    def test_no_overstemming(self):
        # Short words and -ss/-us/-is endings must NOT be folded together.
        from lorehound.search_index import tokenize
        self.assertEqual(tokenize("status"), ["status"])            # not "statu"/"stat*"
        self.assertEqual(tokenize("success"), ["success"])         # -ss kept
        self.assertEqual(tokenize("arms"), ["arms"])               # <=4 chars kept

    def test_worked_example_attaches_to_parent_topic(self):
        from lorehound.rules import _chunks_for_doc
        text = (
            "[[page 41]]\n## PLAYER CHARACTERS\n### AGING\n"
            "Your character ages over their career and may suffer for it.\n"
            "##### ~~**EXAMPLE**~~\n"
            "He rolls a D8 for age effects and it comes up under his number of "
            "terms, so he decreases one attribute by a step.\n"
        )
        chunks = _chunks_for_doc("Twilight 2000 (4E)/Core.pdf", text)
        ex = [c for c in chunks if c.section.endswith("Example")]
        self.assertTrue(ex, "example chunk should exist")
        # It keeps the parent topic instead of an orphan banner…
        self.assertIn("aging", ex[0].section.lower())
        # …and the strikethrough/bold noise is gone from every breadcrumb.
        self.assertFalse(any("~~" in c.section or "**" in c.section for c in chunks))

    def test_example_is_retrievable_by_topic(self):
        from lorehound.rules import _chunks_for_doc
        text = (
            "[[page 41]]\n## PLAYER CHARACTERS\n### AGING\n"
            "Your character ages over their career.\n"
            "##### ~~**EXAMPLE**~~\n"
            "He rolls a D8 for age effects under his number of terms and loses a step.\n"
        )
        idx = SearchIndex()
        idx.build(_chunks_for_doc("Twilight 2000 (4E)/Core.pdf", text))
        hits = idx.search("aging", game="Twilight 2000 (4E)")
        self.assertTrue(hits)
        self.assertIn("aging", hits[0].chunk.section.lower())  # the rescued example surfaces


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


class TestCategorizationRouting(unittest.TestCase):
    """Committed (CI-safe) categorization-neutrality guard. Chunking/classification run
    at index time over cached text, so a change to rules.py can silently re-route
    content between categories. scripts/categorization_snapshot.py guards this against
    the real cache (local only — copyrighted); this pins the major routing decisions on
    synthetic inputs so a re-route is caught in CI. If an intended routing change lands
    here, update these expectations deliberately."""

    def test_prose_routes_rules_vs_reference(self):
        from lorehound.rules import _chunks_for_doc
        text = (
            "[[page 10]]\n## COMBAT\n### MAKING ATTACKS\n"
            "To attack roll the appropriate dice and compare to the target number "
            "described here in full detail for the players.\n"
            "[[page 250]]\n## INDEX\n### A\n"
            "armor 12, attack 10, ammo 14, aging 41, encumbrance 19 listed here alphabetically.\n"
        )
        by_section = {c.section: c.category for c in _chunks_for_doc("SynthGame/Core.pdf", text)}
        self.assertEqual(by_section.get("MAKING ATTACKS"), "rules")
        self.assertEqual(by_section.get("A"), "reference")   # the alphabetical index

    def test_tables_route_by_content(self):
        from lorehound.rules import _tables_for_doc
        tables = [
            {"page": 100, "chapter": "Weapons", "title": "Assault Rifles", "rows": [
                ["Weapon", "Damage", "Range", "Weight", "Cost"],
                ["M16", "3", "100", "3.5", "500"], ["AK-74", "3", "90", "3.6", "450"]]},
            {"page": 120, "chapter": "Vehicles", "title": "Military Vehicles", "rows": [
                ["Vehicle", "Armor", "Speed", "Crew", "Cost"],
                ["M1 Abrams", "20", "70", "4", "9000000"], ["HMMWV", "4", "120", "4", "60000"]]},
            {"page": 40, "chapter": "Skills", "title": "Difficulty Modifiers", "rows": [
                ["Difficulty", "DM"], ["Easy", "+4"], ["Average", "+0"], ["Difficult", "-2"]]},
        ]
        by_section = {c.section: c.category for c in _tables_for_doc("SynthGame/Core.pdf", tables)}
        self.assertEqual(by_section.get("Weapons › Assault Rifles"), "items")
        self.assertEqual(by_section.get("Vehicles › Military Vehicles"), "transport")
        self.assertEqual(by_section.get("Skills › Difficulty Modifiers"), "tables")


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
