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

    def test_vehicle_table_is_transport_despite_weapon_column(self):
        from lorehound.pdf_tables import classify_table

        # A vehicle carries a MAIN WEAPON + REL column, but it's transport, not a
        # weapon catalogue — the VEHICLE signal must win first.
        rows = [["VEHICLE", "TYPE", "REL", "MAIN WEAPON", "PRICE"],
                ["M1 Abrams", "MBT", "5", "105mm", "900,000"]]
        self.assertEqual(classify_table("Weapons, Vehicles & Gear", rows), "transport")

    def test_chapter_fallback_routes_via_profile(self):
        from lorehound import sources
        from lorehound.pdf_tables import classify_table

        # Header gives no category signal; the Traveller profile's item/transport
        # chapters route these header-less tables (case-insensitive on the chapter).
        prof = sources.profile_for("Traveller (Mongoose)")
        self.assertIsNotNone(prof)
        gear = [["NAME", "TL", "MASS", "COST"], ["Comm", "8", "1", "100"]]
        self.assertEqual(classify_table("EQUIPMENT", gear, prof), "items")
        veh = [["NAME", "TL", "SKILL", "COST"], ["ATV", "8", "Drive", "30000"]]
        self.assertEqual(classify_table("VEHICLES", veh, prof), "transport")
        self.assertEqual(classify_table("COMMON SPACECRAFT", veh, prof), "transport")

    def test_chapter_fallback_is_profile_driven_not_hardcoded(self):
        from lorehound.pdf_tables import classify_table

        # With no profile, the chapter name alone must NOT force-route: routing is
        # now profile-supplied, so these header-less tables fall through to "rules".
        gear = [["NAME", "TL", "MASS", "COST"], ["Comm", "8", "1", "100"]]
        veh = [["NAME", "TL", "SKILL", "COST"], ["ATV", "8", "Drive", "30000"]]
        self.assertEqual(classify_table("EQUIPMENT", gear), "rules")
        self.assertEqual(classify_table("VEHICLES", veh), "rules")
        self.assertEqual(classify_table("COMMON SPACECRAFT", veh), "rules")


class TestExplodeToItems(unittest.TestCase):
    def test_catalog_explodes_to_per_item_picks(self):
        from lorehound.cogs.rules_cog import _explode_to_items
        from lorehound.search_index import Chunk, SearchHit

        table = Chunk("T2K", "Core", "transport", "US Vehicles", "p. 121", "veh",
                      rows=[["VEHICLE", "TYPE", "REL", "ARMOR", "WEAPON"],
                            ["M1 Abrams", "MBT", "5", "11", "105mm"],
                            ["M151", "Car", "5", "1", "–"]])
        items = _explode_to_items([SearchHit(chunk=table, score=10.0)], "M1 Abrams")
        names = [h.chunk.section.split("›")[-1].strip() for h in items]
        self.assertIn("M1 Abrams", names)
        self.assertIn("M151", names)
        self.assertEqual(names[0], "M1 Abrams")        # query match floats to top
        self.assertGreaterEqual(items[0].score, 0.6)   # → opens directly

    def test_narrow_table_not_exploded(self):
        from lorehound.cogs.rules_cog import _explode_to_items
        from lorehound.search_index import Chunk, SearchHit

        small = Chunk("T2K", "Core", "transport", "Vehicle Features", "p. 90", "x",
                      rows=[["FEATURE", "EFFECT"], ["4WD", "off-road"]])
        out = _explode_to_items([SearchHit(chunk=small, score=5.0)], "4wd")
        self.assertEqual(len(out), 1)                  # kept as-is, not exploded
        self.assertEqual(out[0].chunk.section, "Vehicle Features")

    def test_chargen_gear_prose_is_rules_not_items(self):
        from lorehound.rules import _category

        # A "GEAR" section under a character-creation chapter is chargen, not gear.
        self.assertEqual(_category("Core.pdf", "Player Characters", "GEAR"), "rules")
        self.assertEqual(_category("Core.pdf", "02. Player Characters", "AMMUNITION"), "rules")
        # …but a real gear chapter is still items.
        self.assertEqual(_category("Core.pdf", "Weapons, Vehicles & Gear", "US Weapons"), "items")

    def test_single_item_statblock_passes_through_not_shredded(self):
        # A T2K-style weapon card: the name is the *title*, the rows are stat pairs —
        # the name column yields ≤1 item, so it must NOT be exploded into "Pistol"…
        from lorehound.cogs.rules_cog import _explode_to_items
        from lorehound.search_index import Chunk, SearchHit

        card = Chunk("T2K", "Core", "items", "Weapons › M1911A1", "p. 99", "x",
                     rows=[["TYPE", "AMMO", "REL", "ROF"], ["Pistol", ".45", "5", "2"],
                           ["BLAST", "RANGE", "MAG", "ARMOR"], ["–", "2", "7", "+1"]])
        out = _explode_to_items([SearchHit(chunk=card, score=18.0)], "M1911A1")
        self.assertEqual(len(out), 1)                       # one whole-table hit
        self.assertEqual(out[0].chunk.section, "Weapons › M1911A1")

    def test_passthrough_normalized_below_item_cards(self):
        # A prose hit (BM25 ~20) must be scaled into [0, 0.5] so it can't outrank a
        # real exploded item card (≤1.0) or auto-open on its own.
        from lorehound.cogs.rules_cog import _explode_to_items
        from lorehound.search_index import Chunk, SearchHit

        prose = Chunk("TRAV", "CSC", "items", "Weaponry › BROADSWORD", "p. 135", "flavor")
        out = _explode_to_items([SearchHit(chunk=prose, score=20.5)], "Broadsword")
        self.assertEqual(len(out), 1)
        self.assertLessEqual(out[0].score, 0.5)


class TestCatalogCards(unittest.TestCase):
    """The catalog item-row index + direct lookup (/item retrieval fix): an item
    name resolves straight to its card, since BM25 buries one row of a long catalog."""

    def _multi(self):  # Traveller/PF-style multi-item weapon catalog
        return Chunk("TRAV", "CSC", "items", "Weaponry › Melee", "p. 136", "x",
                     rows=[["WEAPON", "TL", "RANGE", "DAMAGE"],
                           ["Broadsword", "1", "Melee", "4D"],
                           ["Blade", "1", "Melee", "2D"],
                           ["Psi Blade", "16", "Melee", "3D"]])

    def _single(self):  # T2K-style single-item stat block (name in the title)
        return Chunk("T2K", "Core", "items", "Weapons › M1911A1", "p. 99", "x",
                     rows=[["TYPE", "AMMO", "REL", "ROF"], ["Pistol", ".45", "5", "2"],
                           ["BLAST", "RANGE", "MAG", "ARMOR"], ["–", "2", "7", "+1"]])

    def test_name_match_score_scale(self):
        from lorehound.search_index import name_match_score

        self.assertEqual(name_match_score("Broadsword", "Broadsword"), 1.0)
        self.assertEqual(name_match_score("blade", "Psi Blade"), 0.7)   # sub-phrase
        # partial: shares a token but neither is a sub-phrase of the other
        self.assertLess(name_match_score("Battle Axe", "Great Axe"), 0.6)
        self.assertGreater(name_match_score("Battle Axe", "Great Axe"), 0.0)
        self.assertEqual(name_match_score("foo", "Bar Baz"), 0.0)       # disjoint

    def test_multi_item_catalog_yields_per_row_cards(self):
        from lorehound.rules import _build_catalog_cards

        cards = _build_catalog_cards([self._multi()])[("TRAV", "items")]
        names = {n for n, _ in cards}
        self.assertEqual(names, {"Broadsword", "Blade", "Psi Blade"})
        bs = next(c for n, c in cards if n == "Broadsword")
        self.assertEqual(bs.rows, [["WEAPON", "TL", "RANGE", "DAMAGE"],
                                   ["Broadsword", "1", "Melee", "4D"]])

    def test_single_item_card_named_by_title_whole_table(self):
        from lorehound.rules import _build_catalog_cards

        cards = _build_catalog_cards([self._single()])[("T2K", "items")]
        self.assertEqual([n for n, _ in cards], ["M1911A1"])    # not "Pistol"
        self.assertEqual(len(cards[0][1].rows), 4)             # whole stat block

    def test_lookup_resolves_name_to_card(self):
        from lorehound.rules import RulesService, _build_catalog_cards

        rs = RulesService(None)
        rs._catalog_cards = _build_catalog_cards([self._multi(), self._single()])
        hit = rs.catalog_card_lookup("TRAV", "items", "Broadsword")[0]
        self.assertEqual(hit.score, 1.0)
        self.assertIn("Broadsword", hit.chunk.section)
        # exact title match in the other book/system resolves too
        self.assertEqual(rs.catalog_card_lookup("T2K", "items", "M1911A1")[0].score, 1.0)
        # an ambiguous stem stays a sub-phrase match (won't auto-open)
        blade = rs.catalog_card_lookup("TRAV", "items", "blade")
        self.assertEqual(max(h.score for h in blade), 1.0)     # exact "Blade"
        self.assertTrue(any(h.score == 0.7 for h in blade))    # "Psi Blade"

    def test_card_title_lenient_where_catalog_name_strict(self):
        from lorehound.rules import _is_card_title

        self.assertTrue(_is_card_title("M1911A1"))             # 4-digit serial guard n/a
        self.assertTrue(_is_card_title("Gauss Rifle"))
        self.assertFalse(_is_card_title("You suffer 2 points."))
        self.assertFalse(_is_card_title("50%"))

    def test_merge_dedupes_keeping_higher_score(self):
        from lorehound.cogs.rules_cog import _merge_item_hits
        from lorehound.search_index import Chunk, SearchHit

        c = Chunk("TRAV", "CSC", "items", "Melee › Broadsword", "p. 136", "x")
        direct = [SearchHit(chunk=c, score=1.0)]
        exploded = [SearchHit(chunk=c, score=0.5)]
        out = _merge_item_hits(direct, exploded)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].score, 1.0)


class TestPostClassificationStages(unittest.TestCase):
    """The post-build refinement is three ordered, named stages; the order is
    load-bearing (re-tag promotes, then the chargen guard claws chargen back,
    then reference clutter is judged over what's still 'rules')."""

    def test_retag_by_content_propagates_across_a_section(self):
        from lorehound.rules import _retag_by_content

        # Two chunks share a section key; one body carries a weapon stat table, so
        # both the table chunk and its sibling prose move to items.
        table = Chunk("T2K", "Core", "rules", "Combat › Rifles", "p. 5",
                      "| WEAPON | ROF |\n| M16 | 5 |")
        prose = Chunk("T2K", "Core", "rules", "Combat › Rifles", "p. 5",
                      "The M16 is the standard issue rifle.")
        keys = ["Combat › Rifles", "Combat › Rifles"]
        _retag_by_content([table, prose], keys)
        self.assertEqual(table.category, "items")
        self.assertEqual(prose.category, "items")        # propagated to the sibling

    def test_guard_chargen_runs_after_retag(self):
        from lorehound.rules import _guard_chargen, _retag_by_content

        # A weapon table under a character-creation chapter: stage 1 promotes it to
        # items, stage 2 must claw it back to rules (its gear mentions are chargen).
        ch = Chunk("T2K", "Core", "rules", "Player Characters › Starting Gear", "p. 9",
                   "| WEAPON | ROF |\n| M16 | 5 |")
        keys = ["Player Characters › Starting Gear"]
        _retag_by_content([ch], keys)
        self.assertEqual(ch.category, "items")           # stage 1 promoted it
        _guard_chargen([ch])
        self.assertEqual(ch.category, "rules")           # stage 2 clawed it back

    def test_reference_clutter_retags_index_and_number_dense(self):
        from lorehound.rules import _retag_reference_clutter

        index_leaf = Chunk("T2K", "Core", "rules", "Index › A", "p. 200",
                           "Aardvark Ambush Armor Assault")
        numbers = Chunk("T2K", "Core", "rules", "Combat › Tables", "p. 50",
                        " ".join(["1", "2", "3", "4", "5", "12", "20", "6",
                                  "hits", "8", "9", "10", "11"]))
        keeper = Chunk("T2K", "Core", "rules", "Combat › Rules", "p. 40",
                       "Roll under your skill to hit the target in melee combat.")
        _retag_reference_clutter([index_leaf, numbers, keeper])
        self.assertEqual(index_leaf.category, "reference")   # single-letter leaf
        self.assertEqual(numbers.category, "reference")      # number-dense
        self.assertEqual(keeper.category, "rules")           # genuine rule untouched


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

    def test_card_title_strips_footnote_marker(self):
        from lorehound.tables import render_item

        # A leaked footnote marker ("BMP-2*") must not show in the card title.
        rows = [["VEHICLE", "TYPE"], ["BMP-2*", "IFV"], ["M1 Abrams", "MBT"]]
        _block, _wide, name = render_item(rows, "BMP-2")
        self.assertEqual(name, "BMP-2")

    def test_name_col_picks_alphabetic_not_longest(self):
        from lorehound.tables import _name_col

        # Short weapon codes (M16) must beat a wordier numeric column (RANGE "1500 m").
        grid = [["WEAPON", "RANGE"], ["M16", "1500 m"], ["M82A1", "1800 m"]]
        self.assertEqual(_name_col(grid), 0)

    def test_item_falls_back_when_no_match(self):
        from lorehound.tables import render_item

        # 3+ row table with no matching row → whole-table fallback (no item name).
        rows = [["WEAPON", "DAMAGE"], ["M16", "3"], ["AK-47", "4"]]
        _block, _wide, name = render_item(rows, "bazooka")
        self.assertIsNone(name)

    def test_single_item_grid_always_renders(self):
        from lorehound.tables import render_item

        # An exploded pick is header + one row → always its card, any query.
        _block, _wide, name = render_item([["WEAPON", "DAMAGE"], ["M16", "3"]], "zzz")
        self.assertEqual(name, "M16")


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


class _Doc:
    def __init__(self, name, text, tables=None):
        self.name, self.text, self.tables = name, text, tables or []


class _FakeDrive:
    def __init__(self, docs):
        self._docs = docs

    def fetch_all(self, force=False):
        return self._docs


class TestIndexingStatus(unittest.TestCase):
    """The indexing flag gates flows (chargen) and the warning UI; the rebuild
    swaps a freshly-built index in atomically."""

    def _service(self):
        from lorehound.rules import RulesService

        doc = _Doc(
            "Twilight: 2000/Core.pdf",
            "## Combat\nFiring a weapon in melee combat needs a close-quarters check.\n",
        )
        return RulesService(_FakeDrive([doc]))

    def test_flag_clear_and_ready_after_refresh(self):
        svc = self._service()
        self.assertFalse(svc.indexing)
        self.assertFalse(svc.ready)            # nothing indexed yet
        summary = svc.refresh()
        self.assertFalse(svc.indexing)         # cleared in finally
        self.assertTrue(svc.ready)             # a non-empty index now exists
        self.assertGreaterEqual(summary["chunks"], 1)

    def test_refresh_swaps_a_fresh_index_object(self):
        svc = self._service()
        svc.refresh()
        first = svc.index
        svc.refresh()
        self.assertIsNot(svc.index, first)     # rebuilt off to the side, then swapped

    def test_indexing_flag_resets_on_error(self):
        from lorehound.rules import RulesService

        class _Boom:
            def fetch_all(self, force=False):
                raise RuntimeError("drive down")

        svc = RulesService(_Boom())
        with self.assertRaises(RuntimeError):
            svc.refresh()
        self.assertFalse(svc.indexing)         # cleared even on failure

    def test_concurrent_refresh_is_rejected(self):
        from lorehound.rules import ReindexInProgress

        svc = self._service()
        # Simulate a refresh already in flight by holding the lock, then a second
        # refresh must bail out instead of starting a duplicate Drive pull.
        self.assertTrue(svc._refresh_lock.acquire(blocking=False))
        try:
            with self.assertRaises(ReindexInProgress):
                svc.refresh()
            self.assertFalse(svc.indexing)     # the rejected call never set the flag
        finally:
            svc._refresh_lock.release()
        # Once the in-flight refresh releases, a fresh refresh works again.
        svc.refresh()
        self.assertTrue(svc.ready)


if __name__ == "__main__":
    unittest.main()
