---
name: onboard-source-book
description: >-
  Playbook for adding a new source book / game system to Lorehound's extraction →
  index → lookup pipeline, especially when it's styled or formatted differently from
  the books already indexed. Use when: a new book extracts poorly (garbled/shattered
  tables, missing spell/feat/item cards, headings not detected, lookups mis-routed);
  you're generalizing an extraction/routing/parsing heuristic to handle a
  differently-styled book; or you're deciding between a derived generic fix and a
  per-system SourceProfile. Encodes the "derive from the document, don't hardcode"
  method and the cache-based validation loop that keeps changes from regressing the
  books already working.
metadata:
  project: lorehound
  version: "1.0.0"
---

# Onboarding a new source book into Lorehound

Lorehound indexes TTRPG rulebooks (PDFs in Google Drive) so Discord commands
(`/lookup`, `/item`, `/spell`, `/feat`, `/hazard`, `/table`, `/class`, `/character`)
can answer from them. Every new book is laid out differently — different fonts,
chapter names, table styles, box formats. This playbook is how to make a new book
work **without hardcoding its quirks into code**, and — critically — **without
regressing the books already indexed**.

## The north star (#62)

**Derive from the document; push system specifics out of code into index-derived
data.** A publisher's font name, chapter names, or category words are *data the book
already tells you* — read them off the book instead of baking them into `if` chains.
Ideal end state: dropping any book into Drive indexes it well with no per-book code.

---

## Pipeline map — where each thing happens

Know where to hook before you touch anything:

| Stage | File | What it does |
| --- | --- | --- |
| PDF → Markdown | `drive_client._pdf_markdown` | headings + chrome + boxed-entry reconstruction, per page |
| Heading detection | `headings.py` (`StyleHeadings`) | per-doc style profiling: finds body size, scores heading styles by size/bold/colour |
| Boxed-entry recovery | `stat_box_extract.py` | reconstructs spell/feat/hazard/… boxes on dense multi-column pages; derives heading font, page chrome, and KIND vocab from the page/ToC |
| Boxed-entry parsing | `stat_boxes.py` | parses `##### **NAME KIND LEVEL**` markdown → `StatBox`; `_accepted_kinds` derives the category vocabulary |
| Table detection | `pdf_tables.py` (subprocess `python -m lorehound.pdf_tables`) | find_tables + word-bucketing; must run in a subprocess (pymupdf4llm corrupts find_tables in-process) |
| Table routing | `pdf_tables.classify_table` | routes a table → rules / items / transport / card / noise by header keywords + chapter |
| Structural detection | `tables.is_ship_statblock` (+ vehicle detection) | recognises a stat block by its col0 content, not by chapter |
| Per-system reconstructors | `pdf_tables` fns + `sources.SourceProfile` | geometric/schema repairs for layouts the generic pass can't recover (career grids, armor schemas, ship spreads). **Profiles hold DATA, not code** — coordinates, column-maps, detect markers |
| Index-time assembly | `rules.py` | builds chunks, assigns categories, builds card stores (`_build_stat_cards`, `_build_catalog_cards`), atomic index swap in `refresh()` |
| Commands / presentation | `cogs/rules_cog.py` | slash commands (thin wrappers over `_lookup`) + `CATEGORIES` badge registry |

Routing decisions run at **index time** (`rules.py` re-runs `classify_table`), not
extraction time — so most routing fixes ship on a **reindex**, no re-extraction.

---

## The four reusable patterns (the method)

Each was proven this session. Reach for these before hardcoding.

### 1. Semantic anchor → derive the styling
Find the **invariant shape** that identifies the thing across all systems, then
read the per-book *styling* off it. The anchor is system-agnostic; the styling is
learned per book.
- Box heading = a short all-caps **NAME** sharing its line with a `KIND N` token to
  its right. That anchor lets you *derive* the heading font + size band from the page
  (`stat_box_extract._detect_box_heads`) instead of hardcoding `GoodOT-CondBold`.
- Ship stat block = **ship-part labels in column 0** (`is_ship_statblock`), wherever
  it sits, whatever the header says.

### 2. Recurrence gate for novel/derived signals
When you derive a vocabulary or set (KIND words, categories, chapter domains), let
**known** values count immediately, but require a **novel** value to *recur* (≥3)
before you trust it. One-off coincidences (a stray `STR 10`, a lone chapter title)
never create false entries; genuine repeated structure does.
- `stat_boxes._accepted_kinds`, `stat_box_extract._detect_kinds`. Also: a ≥3-letter
  rule dropped 2-letter stats (HP/AC/DC) from ever being a "KIND".

### 3. Union with the existing defaults (additive → can't regress)
Generalize by **union-ing** derived signals with the current hardcoded defaults.
Result: behaviour is **identical** on the already-working books (they derive the
same value they used to hardcode), and you only *add* coverage for new ones. You can
never do worse.
- Chrome fonts/tab-words = `{Gin} ∪ derived-from-ToC`; category vocab = known kinds
  ∪ recurring-novel. Guard: exclude the page's body/heading fonts from any derived
  "chrome" set so real content is never dropped.

### 4. Structural content detection beats positional/chapter heuristics
Route by **what a table contains** (its col0 signature), not **where it sits**
(chapter). See the dead-end below for why chapter routing fails.

---

## The validation loop (do this for EVERY extraction/parse/routing change)

There is no live bot in-session, and the PDFs live in Drive, not the repo. But the
**cache is your corpus**: `cache/*.json` holds every book's extracted output.

```python
# cache dict keys: v, mdv, tbv, modifiedTime, text (markdown str), tables (list)
# each table dict: {page, chapter, section, title, rows}
import json, glob, os
files = [f for f in glob.glob("cache/*.json") if not os.path.basename(f).startswith(".")]
for f in files:
    d = json.load(open(f)); text, tables = d["text"], d["tables"]
    ...  # run your changed function across every book, tally, spot-check
```

**Rules:**
1. Run the changed function across **all** cached books, not just the new one.
2. Measure the **delta**: counts before/after per category, and the *direction* of
   every change.
3. **Spot-check concrete samples** — print titles + header rows of what changed.
   Aggregate safety is NOT proof: the chapter-routing derivation looked perfectly
   safe by counts ("only rules→transport, never un-routes") but the spot-check showed
   it was dragging **animal stat blocks, "Spacecraft Quirks" flavor tables, and
   deck-plan LEGENDs** into `/transport`. Always eyeball the rows.
4. Confirm the already-working books are **unchanged** (e.g. spell/feat counts
   identical before/after).
5. Add unit tests, but treat the corpus run as the real gate.

**Validation debt to be honest about:** the cache validates *data*, not *live command
behaviour*. Recovered entries only appear after a **reindex**; command UX needs a
running bot. State this plainly in the PR. See `[[deploy-version-bump-needs-bot-restart]]`:
`MD_VERSION`/`TABLE_VERSION` bumps require a full re-extraction (bot down ~30 min);
parser/render/index-time changes are restart-only.

---

## Step-by-step for a new book

1. **Land it & inspect.** Reindex, then load its cache file. How many chunks/tables?
   What chapters (`{t['chapter'] for t in tables}`)? Run `classify_table` over its
   tables — what routes where, what falls to `noise`/`rules`?
2. **Diagnose failure modes**, cheapest first:
   - Headings blobbed? → `StyleHeadings` / ToC injection (`headings.py`).
   - Boxed entries lost/scrambled? → `stat_box_extract` (does the KIND/anchor fire?
     is the heading font derived? is chrome dropping real text?).
   - Tables shattered or mis-routed? → `classify_table` header keywords, or a
     structural detector (`is_ship_statblock`), before reaching for a profile.
3. **Prefer a derived generic fix** (patterns 1–4). Only when a layout is genuinely
   un-derivable, add a `sources.SourceProfile` — and keep the per-system part as
   **data** (coordinates, column-maps, detect markers), consumed by generic code.
4. **Validate against the whole corpus** (loop above). No regressions on existing
   books; spot-check the new book's samples.
5. **Ship** behind the version/validation gate; note the reindex/re-extraction need.

---

## Known dead-ends (don't retry as-is)

- **Deriving `item_chapters`/`transport_chapters` from the document** (a chapter is
  "single-domain" if its header-routed tables are unanimously one domain). Corpus-
  proven *additive* and the hardcoded set is genuinely incomplete — BUT ship chapters
  **mix** ship stat blocks with rules/flavor/reference tables, and chapter-level
  routing can't tell a shattered ship fragment from an animal/quirks/legend table.
  It mis-routed real rules content into `/transport`. The right tool is **structural**
  ship/vehicle detection (below), not chapter routing.

---

## Worked examples (this session)

- **Box heading font** — hardcoded `GoodOT-CondBold` → derived from the `NAME + KIND N`
  anchor (`_detect_box_heads`). Any publisher's display font now works.
- **Page chrome** — hardcoded `Gin` font + `_TAB_WORDS` → derived from the book's ToC
  titles (`_derive_chrome`), body/heading fonts guarded out.
- **Box category vocab** — hardcoded `SPELL|FEAT|…` → derived with the recurrence gate
  (`_accepted_kinds`); recovered ~39 previously-dropped PF entries (item/hazard/rune/
  snare), then surfaced them (`_build_stat_cards` generic, `CATEGORIES` badges,
  `/hazard` command).
- **Vehicle stat-block detection (IN PROGRESS)** — near-miss analysis of the cache
  showed `is_ship_statblock` misses Traveller **vehicle** blocks: their col0 signature
  is `[tl, skill, agility, speed (cruise), range (cruise), crew, passengers, cargo,
  hull, shipping, cost]` (≈32 tables), only 2 *ship-component* words so below the
  `hits ≥ max(3, n//2)` threshold. The fix is a structural `is_vehicle_statblock`
  keyed on ≥3 distinctive vehicle-stat labels (agility + speed/range(cruise) +
  passengers/shipping are near-unique to vehicle blocks) — validate against the whole
  corpus so robot chassis blocks and proficiency/rules tables (the false-positive
  risks that also carry a stray "armour"/"weapon") don't get caught.

Cross-refs in memory: `[[lorehound-chargen-generic-vision]]`,
`[[lorehound-extraction-roadmap]]`, `[[lorehound-open-work]]`,
`[[deploy-version-bump-needs-bot-restart]]`.
