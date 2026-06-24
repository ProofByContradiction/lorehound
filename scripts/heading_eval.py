"""Heading-detection eval harness — score detectors against each PDF's embedded
Table of Contents (the publisher's own chapter list) as ground truth.

Usage (from the repo root, with the venv active):

    python scripts/heading_eval.py <book.pdf | drive_file_id> [more...]

Local .pdf paths are read directly; anything else is treated as a Google Drive
file id and downloaded via the configured service account (see README). Reports,
per book, the ToC-recall and total heading count for:

    default(size)  — pymupdf4llm's built-in font-size IdentifyHeaders
    StyleHeadings  — our bold/colour/size detector + demote_noise post-pass
    Style+ToC      — StyleHeadings plus ToC chapter-heading injection

ToC-recall = fraction of ToC titles that show up as a heading on (or just
before) their page. Books without an embedded ToC report "no-ToC" and are still
useful for eyeballing total heading counts.
"""
import os
import re
import sys

import fitz
import pymupdf4llm
from pymupdf4llm.helpers.pymupdf_rag import IdentifyHeaders

from lorehound.headings import StyleHeadings, demote_noise_doc, inject_toc_headings

_CACHE = "/tmp/lh_eval_pdfs"


def _toks(s):
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if len(t) > 2}


def _by_page(doc, hdr, demote=False, inject=False):
    texts = [p["text"] for p in pymupdf4llm.to_markdown(doc, hdr_info=hdr, page_chunks=True)]
    if demote:
        texts = demote_noise_doc(texts)
    if inject:
        texts = inject_toc_headings(doc, texts)
    return {
        i: [re.sub(r"[*_`#]", "", l).strip() for l in t.splitlines() if l.lstrip().startswith("#")]
        for i, t in enumerate(texts)
    }


def _recall(doc, hbp):
    toc = doc.get_toc()
    hit = 0
    for _level, title, page in toc:
        tt = _toks(title)
        if not tt:
            hit += 1
            continue
        hit += any(
            len(tt & _toks(" ".join(hbp.get(p, [])))) / len(tt) >= 0.6
            for p in range(page - 3, page + 1)
        )
    return hit, len(toc)


def _resolve(arg):
    if arg.lower().endswith(".pdf") or os.path.exists(arg):
        return arg
    os.makedirs(_CACHE, exist_ok=True)
    path = os.path.join(_CACHE, f"{arg}.pdf")
    if not os.path.exists(path):
        from lorehound.config import Config
        from lorehound.drive_client import DriveClient

        cfg = Config.load()
        dc = DriveClient(
            folder_id=cfg.drive_folder_id,
            credentials_file=cfg.google_credentials_file,
            credentials_json=cfg.google_credentials_json,
        )
        open(path, "wb").write(dc._download_bytes(arg))
    return path


def triage(cache_dir="cache"):
    """Sweep every cached book and flag the ones that need attention: no embedded
    ToC (rely on StyleHeadings alone), or sub-perfect Style+ToC recall (likely a
    page-offset bug or unusual styling). 'before' = headings in the current cache
    (font-size path); 'after' = Style+ToC. One extraction per book."""
    import glob
    import json

    rows = []
    for jf in sorted(glob.glob(os.path.join(cache_dir, "*.json"))):
        fid = os.path.basename(jf)[:-5]
        try:
            cached = json.load(open(jf)).get("text", "")
        except Exception:
            continue
        if cached.count("[[page ") < 10:
            continue  # skip tiny docs / google-doc exports
        before = sum(1 for l in cached.splitlines() if l.lstrip().startswith("#"))
        try:
            doc = fitz.open(_resolve(fid))
        except Exception as e:
            rows.append((fid, 0, 0, before, 0, None, f"DOWNLOAD-FAIL {e}"))
            continue
        toc = doc.get_toc()
        hbp = _by_page(fitz.open(_resolve(fid)), StyleHeadings(doc), demote=True, inject=True)
        after = sum(len(v) for v in hbp.values())
        hit, total = _recall(doc, hbp) if toc else (0, 0)
        rec = hit / total if total else None
        flags = []
        if not toc:
            flags.append("NO-TOC")
        elif rec < 0.9:
            flags.append("LOW-RECALL")
        rows.append((fid, doc.page_count, total, before, after, rec, " ".join(flags)))

    rows.sort(key=lambda r: (r[5] is not None, r[5] if r[5] is not None else -1))
    print(f"{'file_id':<16} {'pp':>4} {'toc':>4} {'before':>6} {'after':>6} {'recall':>7}  flags")
    for fid, pp, toc_n, before, after, rec, flags in rows:
        rc = f"{rec*100:.0f}%" if rec is not None else "no-ToC"
        print(f"{fid[:16]:<16} {pp:>4} {toc_n:>4} {before:>6} {after:>6} {rc:>7}  {flags}")


def main(args):
    methods = [
        ("default(size)", lambda d: IdentifyHeaders(d), {}),
        ("StyleHeadings", lambda d: StyleHeadings(d), {"demote": True}),
        ("Style+ToC", lambda d: StyleHeadings(d), {"demote": True, "inject": True}),
    ]
    for arg in args:
        path = _resolve(arg)
        doc = fitz.open(path)
        print(f"=== {os.path.basename(path)}: {doc.page_count} pages, ToC={len(doc.get_toc())} ===")
        for name, mk, kw in methods:
            d = fitz.open(path)
            hbp = _by_page(d, mk(d), **kw)
            hit, total = _recall(d, hbp)
            tot = sum(len(v) for v in hbp.values())
            rc = f"{hit}/{total}" if total else "no-ToC"
            print(f"  {name:14} ToC-recall {rc:>7}   total-headings {tot}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--triage":
        triage(*sys.argv[2:])
    else:
        main(sys.argv[1:])
