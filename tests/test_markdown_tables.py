"""Tests for the markdown pipe-table harvester (lorehound/markdown_tables.py).

Pure-logic on synthetic markdown (runs in CI): it pins the recovery of labelled GFM
tables — the ones ``find_tables`` delabels — from a document's extracted markdown.
"""

import unittest

from lorehound.markdown_tables import MarkdownTable, _dedupe_header_cell, harvest_tables
from lorehound.rules import _markdown_table_chunk, _reconcile_markdown_tables
from lorehound.search_index import Chunk

_MD = """\
[[page 16]]
## **STEP 2:** INSTALL DRIVES
Some prose about drives.

##### **Thrust Potential**

|Manoeuvre Drive Rating|0|1|2|
|---|---|---|---|
|**% of Hull**|0.5%|1%|2%|
|**Manoeuvre TL**|9|9|10|

[[page 17]]
#### **INSTALL ARMOUR**

|Hull Armour Armour|TL|Cost Per Ton|
|---|---|---|
|Titanium Steel|7|Cr50000|
|Crystaliron|10|Cr200000|
"""


class TestHarvest(unittest.TestCase):
    def test_recovers_labelled_tables_with_page_and_title(self):
        tables = harvest_tables(_MD)
        self.assertEqual(len(tables), 2)
        thrust, armour = tables
        self.assertEqual((thrust.page, thrust.title), (16, "Thrust Potential"))
        self.assertEqual((armour.page, armour.title), (17, "INSTALL ARMOUR"))

    def test_row_labels_and_data_are_intact(self):
        armour = harvest_tables(_MD)[1]
        self.assertEqual(armour.header, ["Hull Armour", "TL", "Cost Per Ton"])   # deduped
        self.assertEqual(armour.rows[1], ["Titanium Steel", "7", "Cr50000"])

    def test_strips_emphasis_from_cells(self):
        thrust = harvest_tables(_MD)[0]
        self.assertEqual(thrust.rows[1][0], "% of Hull")   # "**% of Hull**" → "% of Hull"

    def test_separator_row_is_dropped(self):
        thrust = harvest_tables(_MD)[0]
        self.assertTrue(all("---" not in c for r in thrust.rows for c in r))

    def test_ignores_prose_and_malformed_tables(self):
        # A pipe line with no separator row is not a GFM table.
        self.assertEqual(harvest_tables("|a|b|\nsome prose\n"), [])
        self.assertEqual(harvest_tables("no tables here at all"), [])

    def test_dedupe_header_cell(self):
        self.assertEqual(_dedupe_header_cell("Hull Configuration Hull Configuration"),
                         "Hull Configuration")
        self.assertEqual(_dedupe_header_cell("Hull Armour Armour"), "Hull Armour")
        self.assertEqual(_dedupe_header_cell("Sensors Sensors"), "Sensors")
        self.assertEqual(_dedupe_header_cell("Stealth Types Type"), "Stealth Types Type")
        self.assertEqual(_dedupe_header_cell("Rating"), "Rating")


_PATH = "Testgame/Book.pdf"
_GOOD = MarkdownTable(17, "Thrust Potential", [
    ["Drive Rating", "0", "1", "2"], ["% of Hull", "0.5%", "1%", "2%"], ["TL", "9", "9", "10"]])


class TestMarkdownReconcile(unittest.TestCase):
    def test_good_table_becomes_a_chunk(self):
        c = _markdown_table_chunk(_PATH, _GOOD)
        self.assertIsNotNone(c)
        self.assertEqual(c.section, "Thrust Potential")
        self.assertEqual(c.locator, "p. 17")
        self.assertEqual(c.category, "tables")

    def test_sparse_worksheet_rejected(self):
        mt = MarkdownTable(10, "Robot Design", [["A", "B", "C"], ["", "", ""], ["", "", ""]])
        self.assertIsNone(_markdown_table_chunk(_PATH, mt))

    def test_single_data_row_rejected(self):
        mt = MarkdownTable(1, "X", [["Name", "Cost"], ["Sword", "10"]])
        self.assertIsNone(_markdown_table_chunk(_PATH, mt))

    def test_reconcile_drops_subsumed_find_tables_chunk(self):
        # A delabelled find_tables chunk whose numbers all appear in the labelled markdown
        # table on the same page is dropped in favour of the labelled one.
        ft_del = Chunk(game="G", source="B", category="tables", section="Table", locator="p. 17",
                       text="t", rows=[["0.5%", "1%", "2%"], ["9", "9", "10"]])
        ft_other = Chunk(game="G", source="B", category="tables", section="Other", locator="p. 99",
                         text="t", rows=[["Weapon", "Damage"], ["Blade", "1d8"]])
        md_chunks, kept = _reconcile_markdown_tables(_PATH, [_GOOD], [ft_del, ft_other])
        self.assertEqual(len(md_chunks), 1)
        sections = [c.section for c in kept]
        self.assertNotIn("Table", sections)       # subsumed by the markdown table → dropped
        self.assertIn("Other", sections)           # unrelated page → kept


if __name__ == "__main__":
    unittest.main()
