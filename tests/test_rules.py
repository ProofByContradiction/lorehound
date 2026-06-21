"""Unit tests for chunking + retrieval — no Discord or network needed.

Focus: definition-entry splitting (one specialty/item per chunk) and the
heading-breadcrumb boost that ranks the defining entry above passing mentions.

Run with:  python -m unittest tests.test_rules   (or pytest)
"""

import unittest

from lorehound.rules import _chunks_for_doc
from lorehound.search_index import Chunk, SearchIndex


def _sections(chunks):
    return [c.section for c in chunks]


class TestDefinitionEntryChunking(unittest.TestCase):
    PATH = "Twilight: 2000/T2K Core.pdf"

    def test_each_entry_becomes_its_own_chunk(self):
        text = (
            "[[page 50]]\n"
            "## RANGED COMBAT SPECIALTIES\n"
            "- 7 **ARCHER:** Gives a +1 modifier to ranged rolls for bows and crossbows.\n"
            "- 7 **SNIPER:** Gives a +1 modifier to ranged rolls for precise long shots.\n"
            "- 7 **RIFLEMAN:** Gives a +1 modifier to ranged rolls for rapid aimed fire.\n"
        )
        chunks = _chunks_for_doc(self.PATH, text)
        self.assertEqual(
            _sections(chunks),
            [
                "RANGED COMBAT SPECIALTIES › ARCHER",
                "RANGED COMBAT SPECIALTIES › SNIPER",
                "RANGED COMBAT SPECIALTIES › RIFLEMAN",
            ],
        )
        # The term stays in the body so each entry reads on its own.
        sniper = next(c for c in chunks if c.section.endswith("SNIPER"))
        self.assertTrue(sniper.text.startswith("SNIPER:"))
        self.assertIn("long shots", sniper.text)
        self.assertEqual(sniper.category, "rules")

    def test_colon_outside_bold_also_splits(self):
        text = (
            "[[page 12]]\n"
            "## ARMOUR\n"
            "**Ballistic Vest**: A flexible flak jacket worn under clothing for cover.\n"
            "**Ceramic Carapace**: Contoured plates of advanced ceramic for hard protection.\n"
        )
        chunks = _chunks_for_doc(self.PATH, text)
        self.assertEqual(
            _sections(chunks),
            ["ARMOUR › Ballistic Vest", "ARMOUR › Ceramic Carapace"],
        )

    def test_plain_prose_is_unaffected(self):
        text = (
            "[[page 3]]\n"
            "## OVERVIEW\n"
            "This chapter explains how characters act during a combat round and "
            "how the referee resolves the order of actions for everyone involved.\n"
        )
        chunks = _chunks_for_doc(self.PATH, text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].section, "OVERVIEW")
        self.assertNotIn("›", chunks[0].section)

    def test_heading_resets_entry_context(self):
        text = (
            "[[page 7]]\n"
            "## DRIVING SPECIALTIES\n"
            "- 7 **BIKER:** Gives a +1 modifier to driving motorcycles on any terrain.\n"
            "## OTHER RULES\n"
            "After the specialties section, ordinary prose resumes and should not "
            "inherit the previous entry breadcrumb at all in any way here.\n"
        )
        chunks = _chunks_for_doc(self.PATH, text)
        self.assertIn("DRIVING SPECIALTIES › BIKER", _sections(chunks))
        # The trailing prose lands under OTHER RULES, NOT under BIKER.
        prose = [c for c in chunks if "ordinary prose" in c.text]
        self.assertEqual(len(prose), 1)
        self.assertEqual(prose[0].section, "OTHER RULES")


class TestHeadingBoost(unittest.TestCase):
    def test_defining_entry_outranks_passing_mention(self):
        defining = Chunk(
            "Twilight: 2000", "Core", "rules",
            "Ranged Combat Specialties › Sniper", "p. 50",
            "Sniper: gives a plus one modifier to ranged rolls at long distance.",
        )
        mention = Chunk(
            "Twilight: 2000", "Core", "rules",
            "Character Creation", "p. 31",
            "During creation you may choose one specialty such as sniper or medic.",
        )
        idx = SearchIndex()
        idx.build([mention, defining])  # build in 'wrong' order on purpose
        hits = idx.search("sniper", game="Twilight: 2000", category="rules")
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.section, "Ranged Combat Specialties › Sniper")
        # the passing mention either ranks below it or is filtered as a weak tail
        mention = [h for h in hits if h.chunk.section == "Character Creation"]
        if mention:
            self.assertGreater(hits[0].score, mention[0].score)


class TestTables(unittest.TestCase):
    def test_two_column_table_renders_clean(self):
        from lorehound.tables import render_table

        rows = ["D6 HIT LOCATION", "1 Legs", "2–4 Torso", "5 Arm", "6 Head"]
        out, messy = render_table(rows)
        self.assertFalse(messy)
        self.assertTrue(out.startswith("```"))
        for word in ("Legs", "Torso", "Head"):
            self.assertIn(word, out)

    def test_ocr_wrap_folds_into_previous_row(self):
        from lorehound.tables import render_table

        # Short enough not to line-wrap, so the folded text stays contiguous.
        rows = ["FACTOR MODIFIER", "Called shot +3", "at long range"]
        out, messy = render_table(rows)
        self.assertFalse(messy)
        self.assertIn("Called shot at long range", out)

    def test_uniform_grid_reconstructs(self):
        from lorehound.tables import render_table

        rows = [
            "SKILL LEVEL DIE TYPE DIE SIZE DESCRIPTION",
            "A D12 12 Elite",
            "B D10 10 Veteran",
            "C D8 8 Experienced",
            "D D6 6 Novice",
        ]
        out, messy = render_table(rows)
        self.assertFalse(messy)  # uniform 4-token rows → real grid
        for word in ("Elite", "Veteran", "Experienced", "DESCRIPTION"):
            self.assertIn(word, out)

    def test_messy_table_is_flagged(self):
        from lorehound.tables import render_table

        rows = ["NAME RANK SERIAL NOTE", "Bob Sergeant 12345 hero of many battles"]
        out, messy = render_table(rows)
        self.assertTrue(messy)

    def test_extract_table_chunk_from_doc(self):
        from lorehound.rules import _tables_for_doc

        text = (
            "[[page 74]]\n"
            "## **HIT LOCATION**\n"
            "**----- Start of picture text -----**<br>\n"
            "D6 HIT LOCATION<br>1 Legs<br>2–4 Torso<br>5 Arm<br>6 Head<br>"
            "**----- End of picture text -----**<br>\n"
        )
        tables = _tables_for_doc("Twilight 2000 (4E)/Core.pdf", text)
        self.assertEqual(len(tables), 1)
        t = tables[0]
        self.assertEqual(t.category, "tables")
        self.assertEqual(t.section, "HIT LOCATION")
        self.assertEqual(t.locator, "p. 74")
        self.assertIn("Legs", t.text)
        self.assertGreaterEqual(len(t.rows), 5)


if __name__ == "__main__":
    unittest.main()
