"""Extraction-quality eval — pin the correct structure of specific tables.

Retrieval quality (scripts/retrieval_eval.py) measures whether the right *passage*
comes back; this measures whether the underlying *table* was extracted faithfully in
the first place. ``find_tables`` and the reconstructors can silently drop a row or a
column (e.g. the D12 bottom row of the T2K "Chance of Success" tables), and today we
only notice when a human spots it. This harness turns each such correction into a
checked fixture: for every entry in ``eval/extraction_gold.json`` it finds the cached
table (by a few identifying ``match_cells``) and verifies its header, row labels and
row count.

    python scripts/extraction_eval.py            # human-readable report
    python scripts/extraction_eval.py --json      # machine-readable summary

It reads ``cache/*.json`` (the copyrighted books can't live in the repo), so it's a
*local* guard, not a CI check — like retrieval_eval.py. To validate an extraction fix,
reindex with force (re-download + re-extract → updates the cache), then re-run.

``known_broken`` entries are advisory — tables we KNOW are mis-extracted; they're
reported but don't gate. The gate accuracy covers the rest, so the eval is a live
regression guard for known-good tables (it exits non-zero when that drops below
``--threshold``). When a fix lands, flip the entry's ``known_broken`` to false so the
table joins the gate.
"""

from __future__ import annotations

import glob
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_GOLD = os.path.join(_ROOT, "eval", "extraction_gold.json")
_CACHE = os.path.join(_ROOT, "cache")
# Gate over non-advisory entries; the seed's only gated table (Military Ranks) passes.
_DEFAULT_THRESHOLD = 0.80


def load_gold(path: str = _GOLD) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("entries", [])


def _norm(cell: object) -> str:
    return " ".join(str(cell or "").strip().upper().split())


def load_cache_tables() -> list[dict]:
    """Every extracted table dict across the cache (each carries page/title/rows).

    Each table is tagged with ``_book`` — a normalized snippet of its source book's
    text — so an entry can be scoped to one book. Page numbers collide across books
    (every book has a page 24), so without this a generic ``match_cells`` like
    ``["1D","Mishap"]`` could resolve to the wrong book's same-numbered page."""
    tables: list[dict] = []
    for path in sorted(glob.glob(os.path.join(_CACHE, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        book = _norm(data.get("text", ""))  # full text: book_match may be any snippet
        for t in data.get("tables") or []:
            t["_book"] = book
            tables.append(t)
    return tables


def _find_table(entry: dict, tables: list[dict]) -> dict | None:
    """The cached table containing all of the entry's identifying ``match_cells``,
    scoped to the entry's ``book_match`` book and preferring the stated page when
    several match."""
    want = {_norm(c) for c in entry.get("match_cells", [])}
    book = _norm(entry["book_match"]) if entry.get("book_match") else ""
    matches = []
    for t in tables:
        if book and book not in t.get("_book", ""):
            continue
        cells = {_norm(c) for r in t.get("rows") or [] for c in r}
        if want and want <= cells:
            matches.append(t)
    if not matches:
        return None
    page = entry.get("page")
    same_page = [t for t in matches if t.get("page") == page]
    return (same_page or matches)[0]


def score_entry(entry: dict, tables: list[dict]) -> dict:
    """Check one gold table's structure against the cached extraction."""
    t = _find_table(entry, tables)
    res = {
        "id": entry["id"],
        "system": entry.get("system", ""),
        "known_broken": bool(entry.get("known_broken")),
        "found": t is not None,
    }
    if t is None:
        res.update(correct=False, detail="no cached table matched its match_cells")
        return res
    rows = t.get("rows") or []
    exp_headers = [_norm(c) for c in entry.get("expect_headers", [])]
    exp_labels = entry.get("expect_row_labels", [])
    # The column-header row isn't always row[0] — title / section / "Spell Level"
    # banner rows can precede it. Check expected headers against whichever row holds
    # the most of them (row[0] when nothing's expected).
    if exp_headers:
        best = max(rows, key=lambda r: sum(h in {_norm(c) for c in r} for h in exp_headers))
        header = {_norm(c) for c in best}
    else:
        header = {_norm(c) for c in (rows[0] if rows else [])}
    labels = {_norm(r[0]) for r in rows[1:] if r}
    missing_headers = [h for h in exp_headers if h not in header]
    missing_labels = [lbl for lbl in exp_labels if _norm(lbl) not in labels]
    min_rows = int(entry.get("min_rows", 0))
    # ``max_rows`` (optional, 0 = no cap) catches OVER-capture — two tables merged
    # into one, or stray lines pulled in — which a min-only check would pass.
    max_rows = int(entry.get("max_rows", 0))
    nrows = len(rows)
    too_few = nrows < min_rows
    too_many = max_rows > 0 and nrows > max_rows
    rows_ok = not too_few and not too_many
    res.update(
        rows=nrows,
        min_rows=min_rows,
        max_rows=max_rows,
        rows_ok=rows_ok,
        too_few=too_few,
        too_many=too_many,
        missing_headers=missing_headers,
        missing_labels=missing_labels,
        correct=(not missing_headers and not missing_labels and rows_ok),
    )
    return res


def summarize(results: list[dict]) -> dict:
    """Two numbers: the gate accuracy over non-advisory entries (the regression
    guard), and the overall structural accuracy over ALL entries (the realistic
    cross-book picture — advisory/known_broken tables count as the failures they
    currently are). Advisory entries are reported apart so the gate isn't dragged
    down by tables we already know are mis-extracted."""
    gated = [r for r in results if not r["known_broken"]]
    advisory = [r for r in results if r["known_broken"]]
    gated_correct = sum(1 for r in gated if r["correct"])
    overall_correct = sum(1 for r in results if r["correct"])
    by_system: dict[str, dict] = {}
    for r in results:
        s = by_system.setdefault(r.get("system", "?"), {"correct": 0, "total": 0})
        s["total"] += 1
        s["correct"] += 1 if r["correct"] else 0
    for s in by_system.values():
        s["accuracy"] = s["correct"] / s["total"] if s["total"] else 1.0
    return {
        "gate_accuracy": (gated_correct / len(gated)) if gated else 1.0,
        "gated_entries": len(gated),
        "gated_correct": gated_correct,
        "advisory_entries": len(advisory),
        "advisory_correct": sum(1 for r in advisory if r["correct"]),
        "overall_accuracy": (overall_correct / len(results)) if results else 1.0,
        "total_entries": len(results),
        "overall_correct": overall_correct,
        "by_system": by_system,
    }


def _print_report(results: list[dict], summary: dict, threshold: float) -> None:
    for r in results:
        if not r["found"]:
            tag = "NOTABLE"
        elif r["correct"]:
            tag = "OK"
        else:
            tag = "BROKEN (known)" if r["known_broken"] else "BROKEN"
        print(f"\n[{tag}] {r['id']}  · {r['system']}")
        if not r["found"]:
            print(f"       {r.get('detail', '')}")
            continue
        bounds = f"min {r['min_rows']}" + (f", max {r['max_rows']}" if r.get("max_rows") else "")
        flag = "" if r["rows_ok"] else ("  <TOO FEW>" if r.get("too_few") else "  <OVER-CAPTURED>")
        print(f"       rows {r['rows']} ({bounds}){flag}")
        if r["missing_headers"]:
            print(f"       missing headers: {r['missing_headers']}")
        if r["missing_labels"]:
            print(f"       missing row labels: {r['missing_labels']}")
    s = summary
    print(
        f"\n— overall structural accuracy: {s['overall_accuracy']:.0%} over all "
        f"{s['total_entries']} tables ({s['overall_correct']} correct) —"
    )
    print(
        f"— gate table-accuracy (known-good): {s['gate_accuracy']:.0%} over "
        f"{s['gated_entries']} entries ({s['gated_correct']} correct); "
        f"{s['advisory_entries']} advisory ({s['advisory_correct']} already correct) —"
    )
    print("— by system:")
    for system, st in sorted(s["by_system"].items()):
        print(f"    {st['accuracy']:.0%}  {st['correct']}/{st['total']}  {system}")
    print(f"  health threshold {threshold:.0%}: {'OK' if s['gate_accuracy'] >= threshold else 'BELOW'}")


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    threshold = _DEFAULT_THRESHOLD
    for a in argv:
        if a.startswith("--threshold="):
            threshold = float(a.split("=", 1)[1])

    tables = load_cache_tables()
    if not tables:
        print("[extraction-eval] empty cache; nothing to evaluate.", file=sys.stderr)
        return 2

    results = [score_entry(e, tables) for e in load_gold()]
    summary = summarize(results)
    if as_json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
    else:
        _print_report(results, summary, threshold)
    return 0 if summary["gate_accuracy"] >= threshold else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
