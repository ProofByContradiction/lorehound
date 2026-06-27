"""Parse Twilight 2000 chargen data that the generic career detector doesn't pick up
— the Childhood D6 table (in prose) and the Military Ranks ladder (a structured table).

The parser is in code; the rulebook data it reads stays in the gitignored cache
(same principle as the career reconstructor). It runs at index time via the
``extract_prose`` hook on the registered T2K system, and its output is stashed on
``RulesService.chargen_aux`` for the flow to read — so promotions step a real rank
ladder sourced from the index rather than anything embedded here.
"""

from __future__ import annotations

import re

# The childhood block lays out six D6 classes, then their skill triples under a
# "SKILLS" header — one triple per blank-line-delimited group, skills comma-
# separated within a group, e.g.:
#   **1. STREET KID** ... **6. AFFLUENCE**  **CLASS** **FAMILY**
#   **SKILLS** Close Combat, Mobility, Recon \n\n\n Driving, Ranged Combat, Survival ...
_CLASS_LABEL = re.compile(r"\d\.\s*([A-Z][A-Z ]+)")
_GROUP_SEP = re.compile(r"\n\s*\n\s*\n")


def parse_childhood(text: str) -> list[tuple[str, list[str]]]:
    """``[(class_name, [skill, skill, skill]), …]`` from the Childhood block, or
    ``[]`` if the document has no recognisable childhood table."""
    start = text.find("CHILDHOOD")
    if start < 0:
        return []
    seg = text[start:start + 2000]
    end = seg.find("MILITARY SERVICE")
    if end > 0:
        seg = seg[:end]
    classes = [re.sub(r"\*+", "", m).strip().title() for m in _CLASS_LABEL.findall(seg)][:6]
    parts = re.split(r"SKILLS\*{0,2}", seg, maxsplit=1)
    if len(parts) < 2:
        return []
    triples: list[list[str]] = []
    for group in _GROUP_SEP.split(parts[1]):
        if not group.strip():
            continue
        skills = [s.strip() for s in re.sub(r"\s+", " ", group).split(",") if s.strip()]
        if skills:
            triples.append(skills)
        if len(triples) == 6:
            break
    if len(classes) != len(triples) or not triples:
        return []  # mismatch → don't ship a half-parsed table
    return list(zip(classes, triples, strict=True))


# The Military Ranks table is a structured grid: a header row of nationality columns
# then one row per rank level (ascending), "–" where a nationality has no equivalent.
_RANK_COLUMNS = ("us", "soviet", "polish", "swedish")


def parse_ranks(tables: list | None) -> dict:
    """The Military Ranks ladder as ``{"columns": [...], "rows": [[r0,r1,r2,r3], …]}``,
    ascending by level (one name per nationality column). ``{}`` if the document has no
    recognisable ranks table. Levels are shared across nationalities (the US column is
    the spine); a column may hold ``–`` where that nation skips a level."""
    for t in tables or []:
        rows = t.get("rows") if isinstance(t, dict) else None
        if not rows or len(rows) < 6:
            continue
        header = [str(c or "").strip().lower() for c in rows[0][:4]]
        if header == list(_RANK_COLUMNS):
            data = [[str(c or "").strip() for c in r[:4]] for r in rows[1:] if any(r)]
            if len(data) >= 6 and all(len(r) == 4 for r in data):
                return {"columns": list(_RANK_COLUMNS), "rows": data}
    return {}


def extract_t2k_prose(text: str, tables: list | None = None) -> dict:
    """``extract_prose`` hook: auxiliary chargen data from one document's text + tables."""
    out: dict = {}
    childhood = parse_childhood(text)
    if childhood:
        out["childhood"] = [(c, list(skills)) for c, skills in childhood]
    ranks = parse_ranks(tables)
    if ranks:
        out["ranks"] = ranks
    return out
