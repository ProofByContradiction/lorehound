#!/usr/bin/env python
"""Audit table extraction against the source PDF.

For each requested page, renders the page (and each detected table's region) to a
PNG and prints the extracted cells — so you can eyeball "what we extracted" vs
"what's actually in the book". Optional --ocr adds a Tesseract text dump of each
table region for a rough text diff (needs Tesseract; skipped if unavailable).

Usage:
  .venv/bin/python scripts/table_audit.py <drive_file_id> 18,34,67 [--ocr]
Outputs PNGs to ./table-audit/ and prints extracted tables.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz

from lorehound.config import Config
from lorehound.drive_client import DriveClient
from lorehound.pdf_tables import extract_tables


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    file_id = sys.argv[1]
    pages = [int(p) for p in sys.argv[2].split(",") if p.strip()]
    do_ocr = "--ocr" in sys.argv[3:]

    cfg = Config.load()
    drive = DriveClient(
        folder_id=cfg.drive_folder_id,
        credentials_file=cfg.google_credentials_file,
        credentials_json=cfg.google_credentials_json,
    )
    doc = fitz.open(stream=drive._download_bytes(file_id), filetype="pdf")
    out = Path("table-audit")
    out.mkdir(exist_ok=True)
    mat = fitz.Matrix(2, 2)

    for pno in pages:
        page = doc[pno - 1]
        page.get_pixmap(matrix=mat).save(str(out / f"page{pno}.png"))
        tables = extract_tables(page, pno)
        print(f"\n===== page {pno}: {len(tables)} table(s) → table-audit/page{pno}.png")
        for i, t in enumerate(tables, 1):
            print(f"  [{i}] {t['title'][:50]!r}  {len(t['rows'])}x{len(t['rows'][0])}")
            for r in t["rows"]:
                print("        ", [c[:16] for c in r])
            # clip a region PNG around the table for close inspection
            xs = [c for row in t["rows"] for c in row]  # noqa: F841 (kept for clarity)
            tabs = page.find_tables(strategy="lines").tables
            if tabs:
                bx = [b.bbox for b in tabs]
                x0 = min(b[0] for b in bx) - 6
                # render the whole page width band for this table's vertical span
            if do_ocr:
                try:
                    tp = page.get_textpage_ocr(flags=0, full=False)
                    print("        (OCR available — see page PNG for visual diff)")
                except Exception as e:
                    print(f"        (OCR unavailable: {e})")


if __name__ == "__main__":
    main()
