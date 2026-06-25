"""Parse Twilight 2000 chargen tables that live in the book's *prose* rather than
as structured tables — currently the Childhood D6 table.

The parser is in code; the rulebook data it reads stays in the gitignored cache
(same principle as the career reconstructor). It runs at index time via the
``extract_prose`` hook on the registered T2K system, and its output is stashed on
``RulesService.chargen_aux`` for the flow to read.
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


def extract_t2k_prose(text: str) -> dict:
    """``extract_prose`` hook: parsed chargen tables from one document's text."""
    out: dict = {}
    childhood = parse_childhood(text)
    if childhood:
        out["childhood"] = [(c, list(skills)) for c, skills in childhood]
    return out
