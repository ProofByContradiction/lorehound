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
    def test_render_cell_grid(self):
        from lorehound.tables import render_table

        rows = [["D6", "HIT LOCATION"], ["1", "Legs"], ["2–4", "Torso"], ["6", "Head"]]
        out, wide = render_table(rows)
        self.assertFalse(wide)  # only 2 columns
        self.assertTrue(out.startswith("```"))
        self.assertIn("─", out)  # header underline rule (borderless style)
        for word in ("Legs", "Torso", "Head", "HIT LOCATION"):
            self.assertIn(word, out)

    def test_render_flags_wide_table(self):
        from lorehound.tables import render_table

        rows = [["A", "B", "C", "D", "E", "F"], ["1", "2", "3", "4", "5", "6"]]
        _out, wide = render_table(rows)
        self.assertTrue(wide)  # >= 6 columns may scroll on mobile

    def test_wide_roll_table_reflows_to_records(self):
        from lorehound.tables import render_table

        rows = [
            ["D10", "INJURY", "LETHAL", "TIME LIMIT", "EFFECTS", "HEAL TIME"],
            ["1", "Crushed toes", "No", "–", "Running is harder", "2D6"],
            ["10", "Severed leg", "Yes", "Stretch", "Cannot run", "Permanent"],
        ]
        out, _wide = render_table(rows)
        self.assertNotIn("```", out)            # records (markdown), not a code block
        self.assertIn("**Die**", out)           # die header denotes the roll
        self.assertIn("(D10)", out)
        self.assertIn("**Lethal:**", out)       # bold sub-header labels
        self.assertIn("Crushed toes", out)      # the row's primary name
        self.assertIn("`1 `", out)              # roll value in a monospaced column

    def test_narrow_roll_table_stays_ansi_columns(self):
        from lorehound.tables import render_table

        rows = [["D6", "HIT LOCATION"], ["1", "Legs"], ["2-4", "Torso"], ["6", "Head"]]
        out, _wide = render_table(rows)
        self.assertIn("```ansi", out)           # crisp ANSI columns (fits a phone)
        self.assertIn("Roll (D6)", out)         # die column relabeled

    def test_tables_for_doc_routes_by_category(self):
        from lorehound.rules import _tables_for_doc

        tables = [
            {
                "page": 74, "chapter": "Combat & Damage", "section": "Hit Location",
                "title": "HIT LOCATION", "category": "rules",
                "rows": [["D6", "HIT LOCATION"], ["1", "Legs"], ["6", "Head"]],
            },
            {
                "page": 103, "chapter": "Weapons, Vehicles & Gear",
                "section": "US Weapons", "title": "US MILITARY WEAPONS",
                "category": "items", "rows": [["WEAPON", "DAMAGE"], ["M16", "3"]],
            },
        ]
        chunks = _tables_for_doc("Twilight 2000 (4E)/Core.pdf", tables)
        self.assertEqual(len(chunks), 2)
        hit = next(c for c in chunks if "HIT LOCATION" in c.section)
        self.assertEqual(hit.category, "tables")  # rules table → /table
        self.assertEqual(hit.section, "Combat & Damage › HIT LOCATION")
        self.assertEqual(hit.locator, "p. 74")
        self.assertEqual(hit.rows[1], ["1", "Legs"])  # cell grid preserved
        weapons = next(c for c in chunks if "WEAPONS" in c.section)
        self.assertEqual(weapons.category, "items")  # weapon table → /item

    def test_table_name_falls_back_when_title_is_prose(self):
        from lorehound.rules import _tables_for_doc

        tables = [{
            "page": 21, "chapter": "Player Characters", "section": "Stockpiles",
            "title": "that in the travel rules, see", "category": "rules",
            "rows": [["UNIT", "DURATION"], ["Round", "5-10 sec"]],
        }]
        chunks = _tables_for_doc("T/C.pdf", tables)
        # The prose-y title is rejected; falls back to the TOC section.
        self.assertEqual(chunks[0].section, "Player Characters › Stockpiles")


class TestClassifyAndCategory(unittest.TestCase):
    def test_career_with_professor_column_is_card(self):
        from lorehound.pdf_tables import classify_table

        # "PROFESSOR" contains the substring "ROF" — must NOT read as a weapon.
        rows = [["CAREER", "DOCTOR", "PROFESSOR", "MANAGER"], ["REQUIREMENTS", "a", "b", "c"]]
        self.assertEqual(classify_table("Player Characters", rows), "card")

    def test_real_weapon_table_still_items(self):
        from lorehound.pdf_tables import classify_table

        rows = [["WEAPON", "DAMAGE", "ROF", "REL"], ["M16", "3", "5", "5"]]
        self.assertEqual(classify_table("Weapons", rows), "items")

    def test_chargen_gear_prose_is_rules_not_items(self):
        from lorehound.rules import _category

        # A "GEAR" section under a character-creation chapter is chargen, not gear.
        self.assertEqual(_category("Core.pdf", "Player Characters", "GEAR"), "rules")
        self.assertEqual(_category("Core.pdf", "02. Player Characters", "AMMUNITION"), "rules")
        # …but a real gear chapter is still items.
        self.assertEqual(_category("Core.pdf", "Weapons, Vehicles & Gear", "US Weapons"), "items")


class TestItemCard(unittest.TestCase):
    def test_single_item_stat_card(self):
        from lorehound.tables import render_item

        rows = [["WEAPON", "DAMAGE", "ROF"], ["M82A1", "5", "5"], ["M16", "3", "4"]]
        block, _wide, name = render_item(rows, "M82A1")
        self.assertEqual(name, "M82A1")            # name → card header
        self.assertIn("Stat", block)
        self.assertIn("Value", block)
        self.assertIn("DAMAGE", block)
        self.assertNotIn("M16", block)             # only the matched item

    def test_name_col_picks_alphabetic_not_longest(self):
        from lorehound.tables import _name_col

        # Short weapon codes (M16) must beat a wordier numeric column (RANGE "1500 m").
        grid = [["WEAPON", "RANGE"], ["M16", "1500 m"], ["M82A1", "1800 m"]]
        self.assertEqual(_name_col(grid), 0)

    def test_item_falls_back_when_no_match(self):
        from lorehound.tables import render_item

        _block, _wide, name = render_item([["WEAPON", "DAMAGE"], ["M16", "3"]], "bazooka")
        self.assertIsNone(name)


class TestRelevanceCutoff(unittest.TestCase):
    def test_min_rel_drops_weak_partials(self):
        from lorehound.search_index import Chunk, SearchIndex

        idx = SearchIndex()
        idx.build([
            Chunk("G", "S", "tables", "Ranged Fire Modifiers", "p1", "ranged fire modifiers"),
            Chunk("G", "S", "tables", "Close Combat Modifiers", "p2", "close combat modifiers"),
            Chunk("G", "S", "tables", "Foraging Modifiers", "p3", "foraging modifiers"),
        ])
        loose = idx.search("ranged fire modifiers", category="tables")
        strict = idx.search("ranged fire modifiers", category="tables", min_rel=0.6)
        self.assertLessEqual(len(strict), len(loose))
        self.assertEqual(strict[0].chunk.section, "Ranged Fire Modifiers")


class TestTableTopic(unittest.TestCase):
    """Topic grouping for the /table browser."""

    def test_name_beats_chapter(self):
        from lorehound.rules import table_topic

        # These live under the "Combat & Damage" chapter but the NAME decides.
        self.assertEqual(table_topic("Combat & Damage", "RADIATION SICKNESS"), "Health")
        self.assertEqual(table_topic("Combat & Damage", "DISEASES"), "Health")
        self.assertEqual(table_topic("Combat & Damage", "HIT LOCATION"), "Combat")

    def test_word_boundary_not_substring(self):
        from lorehound.rules import table_topic

        # "FORAGING" must be Travel, not Character via the substring "aging".
        self.assertEqual(table_topic("Travel", "FORAGING MODIFIERS"), "Travel")

    def test_character_and_chapter_fallback(self):
        from lorehound.rules import table_topic

        self.assertEqual(table_topic("Player Characters", "ATTRIBUTE SCORES"), "Character")
        self.assertEqual(table_topic("Player Characters", "Introduction"), "Character")

    def test_filename_chapter_is_other(self):
        from lorehound.rules import table_topic

        # Traveller's no-ToC tables have chapter "pdf"; with no name keyword → Other.
        self.assertEqual(table_topic("pdf", "Zaxquib Index"), "Other")
        # …but a recognizable name still classifies.
        self.assertEqual(table_topic("pdf", "Mustering Out Benefits"), "Character")


if __name__ == "__main__":
    unittest.main()
