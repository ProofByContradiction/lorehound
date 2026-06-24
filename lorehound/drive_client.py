"""Google Drive access: list a folder (recursively), download files, extract text.

Authenticates with a *service account* (no interactive OAuth). You create the
service account once, share your rules folder with its email, and provide the
key either as a file path (GOOGLE_CREDENTIALS_FILE) or as the raw JSON
(GOOGLE_CREDENTIALS_JSON, handy for hosts that only offer env vars). See README.

PDF text is extracted with PyMuPDF (pymupdf4llm) to Markdown — it handles
multi-column reading order and tables far better than pypdf, and the Markdown
headings let us chunk by section. We pin pymupdf4llm 0.3.4, whose heading
detection (font-size histogram) + ``column_boxes`` reading order are pure-Python
and never pull pymupdf-layout (-> onnxruntime + numpy, ~100MB) — that ML layout
model is an opt-in extra we don't install. Extracted text is cached to disk
(keyed by Drive file id + last-modified time + extractor version) so
restarts/syncs only re-download files that actually changed.
"""

from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .pdf_tables import classify_table, extract_tables

# Read-only access is all we need.
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Google-native types we know how to export to plain text.
GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_FOLDER = "application/vnd.google-apps.folder"

# Markdown extraction lineage. Bump when the to_markdown path itself changes so
# stale Markdown is recomputed even though the source file is unchanged.
MD_VERSION = "pymupdf-md-styleheadings-v1"
# Table extraction lineage (pdf_tables). Independent of MD_VERSION so a
# markdown-only change reuses the (unchanged) tables instead of re-detecting them.
TABLE_VERSION = "find-tables-lines-v4-travcareers"
# Caches written before the split-versioning scheme; their tables already match
# TABLE_VERSION's logic, so we can reuse them without re-running detection.
_LEGACY_TABLE_VERSIONS = {"pymupdf-md-v2-tables"}
# Combined stamp for the "everything is current" fast path.
EXTRACT_VERSION = f"{MD_VERSION}+{TABLE_VERSION}"


@dataclass
class DriveDoc:
    file_id: str
    name: str          # subfolder-aware label, e.g. "Twilight: 2000/T2K Lore.pdf"
    mime_type: str
    text: str
    # Structured tables recovered from the PDF: each
    # {page, chapter, section, title, category, rows}.
    tables: list[dict] = field(default_factory=list)


class DriveNotConfigured(RuntimeError):
    pass


class DriveClient:
    def __init__(
        self,
        folder_id: str,
        credentials_file: str | None = None,
        credentials_json: str | None = None,
        cache_dir: str | None = "cache",
    ) -> None:
        if not folder_id:
            raise DriveNotConfigured("No DRIVE_FOLDER_ID configured.")
        self.folder_id = folder_id
        self._credentials_file = credentials_file
        self._credentials_json = credentials_json
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._service = None

    def _build_service(self):
        # Imported lazily so the bot still starts when Drive isn't set up.
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        if self._credentials_json:
            info = json.loads(self._credentials_json)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=SCOPES
            )
        elif self._credentials_file:
            creds = service_account.Credentials.from_service_account_file(
                self._credentials_file, scopes=SCOPES
            )
        else:
            raise DriveNotConfigured("No Google credentials configured.")
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    @property
    def service(self):
        if self._service is None:
            self._service = self._build_service()
        return self._service

    # --- Listing ------------------------------------------------------------

    def list_files(self) -> list[dict]:
        """List every non-folder file under the configured folder, recursively.

        Each returned dict gains a ``path`` key (e.g. ``Twilight: 2000/T2K
        Lore.pdf``) so search results can cite which subfolder a doc came from.
        """
        files: list[dict] = []
        self._walk(self.folder_id, "", files, seen=set(), depth=0)
        return files

    def _walk(
        self,
        folder_id: str,
        prefix: str,
        files: list[dict],
        seen: set[str],
        depth: int,
    ) -> None:
        # Guard against shortcut cycles / pathological nesting.
        if folder_id in seen or depth > 10:
            return
        seen.add(folder_id)

        page_token = None
        while True:
            resp = (
                self.service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                    pageToken=page_token,
                    pageSize=100,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for f in resp.get("files", []):
                if f["mimeType"] == GOOGLE_FOLDER:
                    self._walk(f["id"], f"{prefix}{f['name']}/", files, seen, depth + 1)
                else:
                    f["path"] = prefix + f["name"]
                    files.append(f)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    # --- Download + extract -------------------------------------------------

    def _download_bytes(self, file_id: str, attempts: int = 3) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        # Retry transient network failures (a connection reset mid-download
        # shouldn't abort a whole reindex of many books).
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                request = self.service.files().get_media(
                    fileId=file_id, supportsAllDrives=True
                )
                buf = io.BytesIO()
                downloader = MediaIoBaseDownload(buf, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                return buf.getvalue()
            except Exception as exc:  # noqa: BLE001 - retry, then re-raise
                last_exc = exc
                if attempt + 1 < attempts:
                    time.sleep(2 * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    def _export_google_doc(self, file_id: str) -> str:
        data = (
            self.service.files()
            .export(fileId=file_id, mimeType="text/plain")
            .execute()
        )
        return data.decode("utf-8") if isinstance(data, bytes) else str(data)

    def _extract_pdf(self, data: bytes, game: str = "") -> tuple[str, list[dict]]:
        """PDF bytes -> (Markdown with one ``[[page N]]`` marker per page,
        structured tables recovered via pdf_tables)."""
        # Tables run in an isolated subprocess (find_tables only), so order no
        # longer matters; keep them first for consistency with the old path.
        tables = self._pdf_tables(data, game=game)
        text = self._pdf_markdown(data)
        return text, tables

    def _pdf_markdown(self, data: bytes) -> str:
        """PDF bytes -> Markdown with one ``[[page N]]`` marker per page.

        Headings come from our own ML-free style detector (bold/colour/size) plus
        the embedded ToC for chapter titles, with diagram / page-number / running-
        label noise demoted — pymupdf4llm's size-only headings missed the styled
        headings in e.g. Mongoose Traveller books, chunking them as blobs. See
        ``lorehound/headings``."""
        import fitz  # PyMuPDF
        import pymupdf4llm

        from .headings import StyleHeadings, demote_noise_doc, inject_toc_headings

        # 0.3.4 is ML-free already; guard for 1.27.x where use_layout() exists.
        if hasattr(pymupdf4llm, "use_layout"):
            pymupdf4llm.use_layout(False)

        doc = fitz.open(stream=data, filetype="pdf")
        try:
            pages = pymupdf4llm.to_markdown(
                doc, hdr_info=StyleHeadings(doc), page_chunks=True, show_progress=False
            )
            texts = [
                p.get("text", "") if isinstance(p, dict) else str(p) for p in pages
            ]
            texts = demote_noise_doc(texts)          # drop page-numbers / repeated labels
            texts = inject_toc_headings(doc, texts)  # add publisher ToC chapter headings
        finally:
            doc.close()
        return "\n\n".join(f"[[page {i}]]\n{t}" for i, t in enumerate(texts, start=1))

    def _pdf_tables(self, data: bytes, game: str = "") -> list[dict]:
        """Recover structured tables (via an isolated subprocess), tagging each
        with its TOC chapter/section and a routing category. ``game`` selects a
        source profile in the subprocess (hybrid indexer; see ``lorehound.sources``)."""
        import os
        import subprocess
        import sys
        import tempfile

        import fitz

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(data)
            tmp = tf.name
        raw: list[dict] = []
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "lorehound.pdf_tables", tmp, game],
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            if proc.stdout.strip():
                raw = json.loads(proc.stdout)
            if proc.returncode != 0:
                print(f"[drive] table subprocess rc={proc.returncode}: {proc.stderr[:200]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[drive] table extraction failed: {exc}")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

        doc = fitz.open(stream=data, filetype="pdf")
        toc = doc.get_toc() or []
        doc.close()

        def chapter_section(page_no: int) -> tuple[str, str]:
            chap = sec = ""
            for level, title, pg in toc:
                if pg > page_no:
                    break
                if level == 1:
                    chap, sec = title, ""
                elif level == 2:
                    sec = title
            return chap.split(".", 1)[-1].strip(), sec

        out: list[dict] = []
        for t in raw:
            chap, sec = chapter_section(t["page"])
            if chap.lower().startswith("contents"):
                continue  # the book's own table of contents, not a game table
            category = classify_table(chap, t["rows"])
            if category == "noise":
                continue
            out.append(
                {
                    "page": t["page"],
                    "chapter": chap,
                    "section": sec,
                    "title": t["title"],
                    "category": category,
                    "rows": t["rows"],
                }
            )
        return out

    def _extract_text(self, f: dict) -> tuple[str, list[dict]]:
        """Extract (text/Markdown, tables) from a file dict; ('', []) if unsupported."""
        mime = f["mimeType"]
        name = f["name"]
        try:
            if mime == GOOGLE_DOC:
                return self._export_google_doc(f["id"]), []
            if mime == "application/pdf" or name.lower().endswith(".pdf"):
                path = f.get("path", name)
                game = path.split("/", 1)[0] if "/" in path else "General"
                return self._extract_pdf(self._download_bytes(f["id"]), game=game)
            if mime.startswith("text/") or name.lower().endswith((".txt", ".md")):
                return (
                    self._download_bytes(f["id"]).decode("utf-8", errors="replace"),
                    [],
                )
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"[drive] failed to read {name}: {exc}")
            return "", []
        return "", []  # spreadsheets, images, etc.

    # --- Cache --------------------------------------------------------------

    def _cache_file(self, file_id: str) -> Path | None:
        return self.cache_dir / f"{file_id}.json" if self.cache_dir else None

    def _read_cache(self, file_id: str, modified: str) -> tuple[str, list[dict]] | None:
        """Return cached (text, tables) if present, current, and same version."""
        data = self._load_cache(file_id, modified)
        if data is None or data.get("v") != EXTRACT_VERSION:
            return None
        return data.get("text", ""), data.get("tables", [])

    def _load_cache(self, file_id: str, modified: str) -> dict | None:
        """Parsed cache JSON for an *unchanged* file (mtime match), else None."""
        path = self._cache_file(file_id)
        if not path or not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except Exception:  # noqa: BLE001 - corrupt cache, just re-fetch
            return None
        return data if data.get("modifiedTime") == modified else None

    def _read_cached_text(self, file_id: str, modified: str) -> str | None:
        """Cached Markdown for an unchanged file, only if produced by the current
        markdown extractor (MD_VERSION). A markdown-method change recomputes it;
        a table-only change reuses it."""
        data = self._load_cache(file_id, modified)
        if data is None or data.get("mdv") != MD_VERSION:
            return None
        text = data.get("text", "")
        return text if text.strip() else None

    def _read_cached_tables(self, file_id: str, modified: str) -> list[dict] | None:
        """Cached tables for an unchanged file, if produced by the current table
        extractor (TABLE_VERSION) or a legacy cache whose tables still match."""
        data = self._load_cache(file_id, modified)
        if data is None:
            return None
        if data.get("tbv") == TABLE_VERSION or data.get("v") in _LEGACY_TABLE_VERSIONS:
            return data.get("tables", [])
        return None

    def _write_cache(
        self, file_id: str, modified: str, text: str, tables: list[dict]
    ) -> None:
        path = self._cache_file(file_id)
        if not path:
            return
        try:
            path.write_text(
                json.dumps(
                    {
                        "v": EXTRACT_VERSION,
                        "mdv": MD_VERSION,
                        "tbv": TABLE_VERSION,
                        "modifiedTime": modified,
                        "text": text,
                        "tables": tables,
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[drive] cache write failed for {file_id}: {exc}")

    def fetch_all(self, force: bool = False) -> list[DriveDoc]:
        """Download and extract text + tables from every supported file (cached).

        ``force=True`` ignores the on-disk cache and re-extracts every file from
        freshly-downloaded bytes — use it to re-run changed extraction *code*
        against unchanged files without bumping ``MD_VERSION``/``TABLE_VERSION``.
        """
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        docs: list[DriveDoc] = []
        for f in self.list_files():
            source = f.get("path", f["name"])
            modified = f.get("modifiedTime", "")
            cached = None if force else self._read_cache(f["id"], modified)
            if cached is not None:
                text, tables = cached
            else:
                is_pdf = f["mimeType"] == "application/pdf" or f[
                    "name"
                ].lower().endswith(".pdf")
                if is_pdf:
                    # Reuse markdown and tables independently: a markdown-method
                    # change recomputes only the Markdown and keeps the tables
                    # (and vice-versa), downloading the bytes only if either is
                    # actually stale. ``force`` skips both reuses → full re-extract.
                    reused_md = None if force else self._read_cached_text(f["id"], modified)
                    reused_tb = None if force else self._read_cached_tables(f["id"], modified)
                    data = (
                        self._download_bytes(f["id"])
                        if reused_md is None or reused_tb is None
                        else None
                    )
                    # Game = top-level Drive folder, selects the source profile.
                    game = source.split("/", 1)[0] if "/" in source else "General"
                    text = reused_md if reused_md is not None else self._pdf_markdown(data)
                    tables = (
                        reused_tb
                        if reused_tb is not None
                        else self._pdf_tables(data, game=game)
                    )
                else:
                    text, tables = self._extract_text(f)
                self._write_cache(f["id"], modified, text, tables)
            if text.strip():
                docs.append(
                    DriveDoc(
                        file_id=f["id"],
                        name=source,
                        mime_type=f["mimeType"],
                        text=text,
                        tables=tables,
                    )
                )
        return docs
