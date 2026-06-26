"""Categorization-neutrality snapshot — guard chunking/classification changes.

Chunking and categorization run at index time over the *cached* extraction output,
so a change to ``rules.py`` (chunk building, ``_category``, the post-passes) can
silently re-route content between ``rules`` / ``items`` / ``transport`` / ``tables``
/ ``card`` / ``reference`` without any test noticing. This tool rebuilds every chunk
straight from ``cache/*.json`` and prints a stable fingerprint — per-category counts,
career/catalog totals, and a content hash over each chunk's (category, breadcrumb,
locator, body-head). Run it before and after a chunking change; a byte-identical
fingerprint proves the change is categorization-neutral.

    python scripts/categorization_snapshot.py                 # print the snapshot
    python scripts/categorization_snapshot.py --json          # machine-readable
    python scripts/categorization_snapshot.py --baseline f.json  # diff vs a saved run

It reads the local cache (the copyrighted books can't live in the repo), so it's a
*local* guard, not a CI check — like scripts/retrieval_eval.py. The cache stores no
doc names, so each file id stands in for its game/book; the comparison is
self-consistent before/after, which is all neutrality needs.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
from collections import Counter

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_CACHE = os.path.join(_ROOT, "cache")


def build_snapshot() -> dict:
    """Rebuild all chunks from the cache and return a fingerprint dict."""
    from lorehound.rules import (
        _build_catalog_names,
        _chunks_for_doc,
        _tables_for_doc,
        detect_careers,
    )

    chunks = []
    for path in sorted(glob.glob(os.path.join(_CACHE, "*.json"))):
        fid = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        name = f"{fid}/{fid}.pdf"
        chunks.extend(_chunks_for_doc(name, data.get("text") or ""))
        chunks.extend(_tables_for_doc(name, data.get("tables") or []))

    catalog = _build_catalog_names(chunks)
    careers = detect_careers(chunks)
    lines = sorted(
        f"{c.category}\t{c.section}\t{c.locator}\t{(c.text or '')[:60]}" for c in chunks
    )
    content_hash = hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]
    return {
        "chunks": len(chunks),
        "by_category": dict(sorted(Counter(c.category for c in chunks).items())),
        "catalog_total": sum(len(v) for v in catalog.values()),
        "careers": sum(len(v) for v in careers.values()),
        "content_hash": content_hash,
    }


def _diff(baseline: dict, current: dict) -> list[str]:
    out = []
    for key in ("chunks", "catalog_total", "careers", "content_hash"):
        if baseline.get(key) != current.get(key):
            out.append(f"  {key}: {baseline.get(key)} -> {current.get(key)}")
    bcat, ccat = baseline.get("by_category", {}), current.get("by_category", {})
    for cat in sorted(set(bcat) | set(ccat)):
        if bcat.get(cat, 0) != ccat.get(cat, 0):
            out.append(f"  by_category[{cat}]: {bcat.get(cat, 0)} -> {ccat.get(cat, 0)}")
    return out


def main(argv: list[str]) -> int:
    snap = build_snapshot()
    if snap["chunks"] == 0:
        print("[snapshot] empty cache; nothing to snapshot.", file=sys.stderr)
        return 2
    baseline_path = next((a.split("=", 1)[1] for a in argv if a.startswith("--baseline=")), None)
    if "--baseline" in argv:
        i = argv.index("--baseline")
        if i + 1 < len(argv):
            baseline_path = argv[i + 1]
    if baseline_path:
        with open(baseline_path, encoding="utf-8") as fh:
            baseline = json.load(fh)
        drift = _diff(baseline, snap)
        if drift:
            print("CATEGORIZATION DRIFT vs baseline:")
            print("\n".join(drift))
            return 1
        print("NEUTRAL — snapshot matches baseline (categorization unchanged).")
        return 0
    print(json.dumps(snap, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
