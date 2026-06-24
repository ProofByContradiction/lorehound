"""Render every /table, /class, and /item card to a text file for UX/UI review.

Instead of typing each lookup in Discord one-by-one, this dumps the rendered card
*content* (tables, career cards, single-item stat cards) for the whole library to
a file you can scroll. ANSI colour codes are stripped (a text file can't show
Discord's colours), so this reviews layout/alignment/structure, not colour.

    python scripts/card_gallery.py                 # all games -> /tmp/lorehound_gallery.txt
    python scripts/card_gallery.py --game Twilight  # one game (substring match)
    python scripts/card_gallery.py --out cards.txt --limit 30

Needs the indexed library (Drive cache), like scripts/retrieval_eval.py.
"""

from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    """Strip ANSI SGR codes and code-fences so the block reads as clean monospace."""
    s = _ANSI.sub("", s)
    return s.replace("```ansi", "").replace("```", "").strip("\n")


def _build_service():
    from scripts.retrieval_eval import build_service

    return build_service()


def _career_text(career) -> str:
    """The career card's sections as plain text (mirrors the Discord card body)."""
    out = [f"🪖 {career.name}" + ("  (assembled)" if career.assembled else "")]
    for s in career.sections:
        if s.rows:
            from lorehound.tables import render_table

            out.append(f"  [{s.label}]")
            out.append("    " + _plain(render_table(s.rows)[0]).replace("\n", "\n    "))
        elif s.label:
            out.append(f"  {s.label}: {s.text}")
        elif s.text:
            out.append(f"  {s.text}")
    out.append(f"  -# {career.source} · {career.locator}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    from lorehound.tables import render_item, render_table

    game_filter = ""
    out_path = "/tmp/lorehound_gallery.txt"
    limit = None
    for i, a in enumerate(argv):
        if a == "--game" and i + 1 < len(argv):
            game_filter = argv[i + 1].lower()
        elif a == "--out" and i + 1 < len(argv):
            out_path = argv[i + 1]
        elif a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])

    svc = _build_service()
    if svc is None:
        print("[gallery] library not available (Drive/cache).", file=sys.stderr)
        return 2

    lines: list[str] = ["# Lorehound card gallery", ""]
    for game in svc.index.games:
        if game_filter and game_filter not in game.lower():
            continue
        chunks = [c for c in svc.index.chunks if c.game == game]
        lines.append("\n" + "#" * 70)
        lines.append(f"# {game}")
        lines.append("#" * 70)

        # /table
        tables = [c for c in chunks if c.category == "tables" and c.rows][:limit]
        lines.append(f"\n=== TABLES ({len(tables)}) ===")
        for c in tables:
            lines.append(f"\n--- {c.section}  ({c.locator}) ---")
            lines.append(_plain(render_table(c.rows)[0]))

        # /class
        careers = list(svc.careers.get(game, {}).values())[:limit]
        lines.append(f"\n=== CLASSES ({len(careers)}) ===")
        for career in sorted(careers, key=lambda c: c.name):
            lines.append("\n" + _career_text(career))

        # /item — one sample single-item card per items table (the first data row)
        items = [c for c in chunks if c.category == "items" and len(c.rows) >= 2][:limit]
        lines.append(f"\n=== ITEMS (one sample per table, {len(items)} tables) ===")
        for c in items:
            sample = next((r for r in c.rows[1:] if any(x.strip() for x in r)), None)
            if not sample:
                continue
            name = next((x for x in sample if x.strip()), "item")
            block, _wide, item_name = render_item(c.rows, name)
            lines.append(f"\n--- {item_name or name}  (from {c.section} · {c.locator}) ---")
            lines.append(_plain(block))

        # /transport — vehicle/ship card per transport table (stat block or catalog row)
        transports = [c for c in chunks if c.category == "transport" and len(c.rows) >= 2][:limit]
        lines.append(f"\n=== TRANSPORT (one sample per table, {len(transports)} tables) ===")
        for c in transports:
            sample = next((r for r in c.rows[1:] if any(x.strip() for x in r)), None)
            name = next((x for x in (sample or [""]) if x.strip()), "vehicle")
            block, _wide, item_name = render_item(c.rows, name)
            lines.append(f"\n--- {item_name or c.section}  (from {c.section} · {c.locator}) ---")
            lines.append(_plain(block))

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"[gallery] wrote {out_path} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
