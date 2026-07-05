"""Small, shared text/grid helpers used across extraction, tables, and careers.

These are the *provably identical* idioms that were duplicated verbatim in several
modules. They are deliberately tiny and behaviour-preserving — the goal is one
definition, not new behaviour. (Tokenizers are intentionally *not* here: each one
has a different regex / min-length tuned to its job, so they stay local.)
"""

from __future__ import annotations

import re
from functools import lru_cache

_WORD_PATHS = ("/usr/share/dict/words", "/usr/dict/words")
# Domain words the BSD/system dictionary (web2) lacks, so the ligature repair will
# accept these reconstructions.
_EXTRA_WORDS = frozenset({
    "pathfinder", "reflex", "modifier", "modifiers", "cantrip", "cantrips",
    "spellcasting", "darkvision", "feats",
    # Common words web2 lacks that would otherwise be "repaired" into a real word
    # — "feet"→"fleet", the "ft" abbreviation→"fit". Protect them explicitly.
    "feet", "ft",
})


@lru_cache(maxsize=1)
def _wordset() -> frozenset[str]:
    """The system word list (lower-cased) plus a few domain words, loaded once."""
    for path in _WORD_PATHS:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                return frozenset(w.strip().lower() for w in fh if w.strip()) | _EXTRA_WORDS
        except OSError:
            continue
    return _EXTRA_WORDS  # no system dictionary present — repair stays a near no-op


def _known(word: str, words: frozenset[str]) -> bool:
    w = word.lower()
    if w in words:
        return True
    for suf in ("s", "es", "ed", "ing"):  # web2 omits many inflections
        if w.endswith(suf) and len(w) > len(suf) + 1 and w[: -len(suf)] in words:
            return True
    return False


_LIG_TOKEN = re.compile(r"[A-Za-z]+")

# Literal Unicode ligature codepoints → their ASCII letters. Distinct from
# repair_ligatures (which restores an "fi"/"fl" a broken CMap collapsed to a bare "f"):
# these are the *composed* glyphs (U+FB00–06) some PDFs keep verbatim. The search
# tokenizer ([a-z0-9]+) can't see through them — "eﬀect" tokenizes to "e"+"ect", so a
# search for "effect" misses it — so normalise them everywhere the text is indexed.
_LIGATURE_MAP = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
})


def normalize_ligatures(text: str) -> str:
    """Replace composed ligature glyphs (ﬀﬁﬂﬃﬄ…) with their ASCII letters, so the text
    both displays cleanly and tokenises for search. Deterministic; safe to apply broadly."""
    return text.translate(_LIGATURE_MAP)


# C0 control characters (U+0000–U+001F) and DEL (U+007F), minus the whitespace that
# carries structure (\t \n \r). PDF extraction can leave stray control bytes mid-text
# — mostly \x08 (backspace), the odd \x07 (bell) — which are invisible but break the
# search tokenizer (they split a word into two tokens) and litter /lookup excerpts.
# Mapping each to None makes str.translate DELETE it (they sit at word boundaries, so
# removing them rejoins nothing and is safe — unlike replacing with a space, which
# would inject spurious gaps before the following ** / space / newline).
_CONTROL_DELETE = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}
_CONTROL_DELETE[0x7F] = None


def strip_control_chars(text: str) -> str:
    """Delete C0 control characters (and DEL) from extracted text, keeping the
    structural whitespace \\t \\n \\r intact. Deterministic; index-time normalization
    in the same spirit as :func:`normalize_ligatures`."""
    return text.translate(_CONTROL_DELETE)


def repair_ligatures(text: str) -> str:
    """Repair ``fi``/``fl`` ligatures that a broken font CMap collapsed to a bare
    ``f`` during extraction ("fre"→"fire", "Refex"→"Reflex", "difcult"→"difficult").

    A token already in the dictionary is left untouched (so "from" / "free" /
    "after" are safe); otherwise the ``i`` / ``l`` / ``fi`` / ``fl`` that makes a
    real word is restored. Index-time only; with no system word list it's a no-op."""
    words = _wordset()
    if len(words) < 1000:  # no real dictionary loaded — don't guess
        return text

    def fix(m: re.Match) -> str:
        tok = m.group(0)
        if "f" not in tok.lower() or _known(tok, words):
            return tok
        for i, ch in enumerate(tok):
            if ch.lower() == "f":
                for ins in ("i", "l", "fi", "fl"):
                    cand = tok[: i + 1] + ins + tok[i + 1 :]
                    if _known(cand, words):
                        return cand
        return tok

    return _LIG_TOKEN.sub(fix, text)


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
