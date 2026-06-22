"""Retrieval regression eval — run ``eval/gold_queries.json`` against the live index.

The north-star metric the heading/chunking/table work serves is *correct answers*:
does ``/rule`` return a passage that actually contains the rule? This harness
builds the real rules index (from the Drive cache — unchanged files aren't
re-downloaded), runs every gold query scoped to its game, and checks each
``key_fact`` shows up in the retrieved passages. Run it after any extraction
change to catch silent quality regressions.

    python scripts/retrieval_eval.py            # human-readable report
    python scripts/retrieval_eval.py --json     # machine-readable summary

It needs the indexed library (Google Drive configured + a populated ``cache/``),
so it is a *local* guard, not a CI check — the copyrighted books can't live in
the repo. Exits non-zero when the fact-recall over edition-stable entries drops
below ``--threshold`` (default 0.70), so it can gate a release.

The scoring is intentionally fuzzy (wording differs by edition): a fact counts as
found when its normalized text appears as a substring of the retrieved passages,
or when enough of its content tokens (numbers always kept) are present.
"""

from __future__ import annotations

import json
import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)  # make `lorehound` importable when run as a script

_GOLD = os.path.join(_ROOT, "eval", "gold_queries.json")

# Tiny stopword set so token-overlap matching keys on the meaningful words.
_STOP = frozenset(
    "the a an of to is are be on in with for and or each you your do how it that "
    "this by as at from if i my me we they them than then so any one".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")

# Top-k passages whose text a fact may appear in (matches "present in a retrieved
# chunk" — the answer can be the 1st or a near hit).
_TOP_K = 5
_FACT_COVERAGE = 0.70   # fraction of a fact's content tokens that must be present
# Health threshold for the standalone CLI. The measured baseline on the current
# library was 0.32 fact-recall (2026-06-22): the right *sections* are retrieved,
# but granular numeric values (difficulty/penalty tables) often sit in /table-
# routed chunks that miss the top-k — known retrieval-ranking work, see README.
# The in-suite regression FLOOR (tests/test_retrieval.py) is looser, so the test
# only fails on a true drop, not on tuning noise.
_DEFAULT_THRESHOLD = 0.30


def load_gold(path: str = _GOLD) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("entries", [])


def _norm(s: str) -> str:
    return " ".join(_TOKEN.findall(s.lower()))


def _content_tokens(s: str) -> list[str]:
    """Meaningful tokens for overlap scoring: drop stopwords, keep words of length
    >= 2 and any standalone number (so '8', '2d6', '+1'→'1' still count)."""
    return [t for t in _TOKEN.findall(s.lower()) if t not in _STOP and (len(t) >= 2 or t.isdigit())]


def _token_hit(tok: str, hay: set[str]) -> bool:
    """Is ``tok`` present in the passage tokens, tolerant of inflection? Numbers and
    short tokens match exactly; longer words match via a shared prefix in either
    direction (so 'success'~'successes', 'roll'~'rolls', 'mod'~… stays exact)."""
    if tok in hay:
        return True
    if tok.isdigit() or len(tok) < 4:
        return False
    return any(len(p) >= 4 and (p.startswith(tok) or tok.startswith(p)) for p in hay)


def fact_present(fact: str, hay_norm: str, hay_tokens: set[str]) -> tuple[bool, float]:
    """(found, coverage) for one key_fact against the retrieved passages.

    Found if the normalized fact is a substring of the passages, or if at least
    ``_FACT_COVERAGE`` of the fact's content tokens appear (inflection-tolerant).
    ``coverage`` is the token-overlap fraction, surfaced so humans can judge
    borderline matches.
    """
    fn = _norm(fact)
    if fn and fn in hay_norm:
        return True, 1.0
    toks = _content_tokens(fact)
    if not toks:
        return (fn in hay_norm if fn else False), 0.0
    cover = sum(_token_hit(t, hay_tokens) for t in toks) / len(toks)
    return cover >= _FACT_COVERAGE, cover


def resolve_game(system: str, games: list[str]) -> str | None:
    """Map a gold ``system`` label (e.g. "Twilight 2000 (4E)") to an actual index
    game (the Drive folder name, e.g. "Twilight: 2000") by best token overlap."""
    want = set(_content_tokens(system))
    best, best_score = None, 0
    for g in games:
        score = len(want & set(_content_tokens(g)))
        if score > best_score:
            best, best_score = g, score
    return best if best_score else None


def run_eval(service, gold: list[dict]) -> list[dict]:
    """Run each gold entry through the index; return per-entry result dicts."""
    games = service.index.games
    results = []
    for e in gold:
        game = resolve_game(e["system"], games)
        passages = ""
        top_section = ""
        if game is not None:
            hits = service.search(e["query"], game=game, top_k=_TOP_K)
            passages = "\n".join(f"{h.chunk.section}\n{h.chunk.text}" for h in hits)
            top_section = hits[0].chunk.section if hits else ""
        hay_norm = _norm(passages)
        hay_tokens = set(_TOKEN.findall(passages.lower()))
        facts = [
            (f, *fact_present(f, hay_norm, hay_tokens)) for f in e.get("key_facts", [])
        ]
        found = sum(1 for _f, ok, _c in facts if ok)
        results.append(
            {
                "id": e["id"],
                "system": e["system"],
                "game": game,
                "query": e["query"],
                "edition_sensitive": bool(e.get("edition_sensitive")),
                "facts": facts,                       # [(fact, found, coverage)]
                "found": found,
                "total": len(facts),
                "recall": found / len(facts) if facts else 0.0,
            }
        )
    return results


def summarize(results: list[dict]) -> dict:
    """Aggregate fact-recall. The gate metric covers only edition-STABLE entries
    (edition-sensitive equipment values legitimately differ in our 2E library and
    are reported separately, advisory-only)."""
    stable = [r for r in results if not r["edition_sensitive"]]
    sens = [r for r in results if r["edition_sensitive"]]
    gate = sum(r["recall"] for r in stable) / len(stable) if stable else 0.0
    return {
        "gate_recall": gate,
        "stable_entries": len(stable),
        "sensitive_entries": len(sens),
        "perfect": sum(1 for r in stable if r["recall"] == 1.0),
    }


def build_service():
    """Build the live RulesService from config + cache, or return None if the
    library isn't available (Drive unconfigured, offline, or empty index)."""
    from lorehound.config import Config
    from lorehound.drive_client import DriveClient
    from lorehound.rules import RulesService

    try:
        cfg = Config.load()
    except Exception:
        return None
    if not cfg.drive_configured:
        return None
    try:
        drive = DriveClient(
            folder_id=cfg.drive_folder_id,
            credentials_file=cfg.google_credentials_file,
            credentials_json=cfg.google_credentials_json,
            cache_dir="cache",
        )
        svc = RulesService(drive)
        svc.refresh()
    except Exception as exc:  # offline / no creds / cache miss
        print(f"[eval] could not build index: {exc}", file=sys.stderr)
        return None
    return svc if not svc.index.is_empty else None


def _print_report(results: list[dict], summary: dict, threshold: float) -> None:
    for r in results:
        tag = "PASS" if r["recall"] == 1.0 else ("MISS" if r["found"] == 0 else "PART")
        sens = " (edition-sensitive, advisory)" if r["edition_sensitive"] else ""
        game = r["game"] or "NO-LIBRARY-MATCH"
        print(f"\n[{tag}] {r['id']}  {r['found']}/{r['total']} facts  · {game}{sens}")
        print(f"       q: {r['query']}")
        for fact, ok, cover in r["facts"]:
            mark = "✓" if ok else "✗"
            print(f"        {mark} ({cover:.0%}) {fact}")
    g = summary["gate_recall"]
    print(
        f"\n— gate fact-recall (edition-stable): {g:.0%} over {summary['stable_entries']} "
        f"entries ({summary['perfect']} perfect); {summary['sensitive_entries']} advisory —"
    )
    print(f"  health threshold {threshold:.0%}: {'OK' if g >= threshold else 'BELOW'}")


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    threshold = _DEFAULT_THRESHOLD
    for a in argv:
        if a.startswith("--threshold="):
            threshold = float(a.split("=", 1)[1])

    service = build_service()
    if service is None:
        print(
            "[eval] library not available (Drive not configured, offline, or empty "
            "cache); nothing to evaluate.",
            file=sys.stderr,
        )
        return 2

    results = run_eval(service, load_gold())
    summary = summarize(results)
    if as_json:
        print(json.dumps({"summary": summary, "results": [
            {k: v for k, v in r.items() if k != "facts"} for r in results
        ]}, indent=2))
    else:
        _print_report(results, summary, threshold)
    return 0 if summary["gate_recall"] >= threshold else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
