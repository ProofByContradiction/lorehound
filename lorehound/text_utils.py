"""Small, shared text/grid helpers used across extraction, tables, and careers.

These are the *provably identical* idioms that were duplicated verbatim in several
modules. They are deliberately tiny and behaviour-preserving — the goal is one
definition, not new behaviour. (Tokenizers are intentionally *not* here: each one
has a different regex / min-length tuned to its job, so they stay local.)
"""

from __future__ import annotations


def acronym_title(s: str) -> str:
    """Normalize an ALL-CAPS label/name for display ("REQUIREMENTS" -> "Requirements",
    "TIME LIMIT" -> "Time Limit"), but keep short all-caps acronyms intact ("EMT"
    stays "EMT", "ROF" stays "ROF"). Mixed-case words pass through untouched."""
    if not s.strip():
        return s.strip()
    out = []
    for w in s.split():
        if w.isalpha() and w.isupper() and len(w) <= 3:
            out.append(w)              # acronym: EMT, FBI, ROF
        elif w.isupper():
            out.append(w.title())      # FIREMAN -> Fireman, ARMS -> Arms
        else:
            out.append(w)              # already mixed-case
    return " ".join(out)


def clean_grid(rows) -> list[list[str]]:
    """Normalize a cell grid: strip each cell (treating ``None`` as "") and drop any
    row that is entirely blank. ``[[(c or "").strip() for c in r] for r in rows if
    any((c or "").strip() for c in r)]`` — the recurring grid-tidy idiom in one place."""
    return [
        [(c or "").strip() for c in r]
        for r in rows
        if any((c or "").strip() for c in r)
    ]
