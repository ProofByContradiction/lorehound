"""Google Drive access: list a folder (recursively), download files, extract text.

Authenticates with a *service account* (no interactive OAuth). You create the
service account once, share your rules folder with its email, and provide the
key either as a file path (GOOGLE_CREDENTIALS_FILE) or as the raw JSON
(GOOGLE_CREDENTIALS_JSON, handy for hosts that only offer env vars). See README.

PDF text is extracted with PyMuPDF (pymupdf4llm) to Markdown — it handles
multi-column reading order and tables far better than pypdf, and the Markdown
headings let us chunk by section. Extracted text is cached to disk (keyed by
Drive file id + last-modified time + extractor version) so restarts/syncs only
re-download files that actually changed.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

# Read-only access is all we need.
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Google-native types we know how to export to plain text.
GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_FOLDER = "application/vnd.google-apps.folder"

# Bump to invalidate all caches when the extraction method/output changes.
EXTRACT_VERSION = "pymupdf-md-v1"


@dataclass
class DriveDoc:
    file_id: str
    name: str          # subfolder-aware label, e.g. "Twilight: 2000/T2K Lore.pdf"
    mime_type: str
    text: str


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

    def _download_bytes(self, file_id: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        request = self.service.files().get_media(
            fileId=file_id, supportsAllDrives=True
        )
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    def _export_google_doc(self, file_id: str) -> str:
        data = (
            self.service.files()
            .export(fileId=file_id, mimeType="text/plain")
            .execute()
        )
        return data.decode("utf-8") if isinstance(data, bytes) else str(data)

    def _extract_pdf(self, data: bytes) -> str:
        """PDF bytes -> Markdown with one ``[[page N]]`` marker per page."""
        import fitz  # PyMuPDF
        import pymupdf4llm

        doc = fitz.open(stream=data, filetype="pdf")
        try:
            pages = pymupdf4llm.to_markdown(
                doc, page_chunks=True, show_progress=False
            )
        finally:
            doc.close()
        out = []
        for i, p in enumerate(pages, start=1):
            md = p.get("text", "") if isinstance(p, dict) else str(p)
            out.append(f"[[page {i}]]\n{md}")
        return "\n\n".join(out)

    def _extract_text(self, f: dict) -> str:
        """Extract plain text/Markdown from one file dict, or '' if unsupported."""
        mime = f["mimeType"]
        name = f["name"]
        try:
            if mime == GOOGLE_DOC:
                return self._export_google_doc(f["id"])
            if mime == "application/pdf" or name.lower().endswith(".pdf"):
                return self._extract_pdf(self._download_bytes(f["id"]))
            if mime.startswith("text/") or name.lower().endswith((".txt", ".md")):
                return self._download_bytes(f["id"]).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"[drive] failed to read {name}: {exc}")
            return ""
        return ""  # spreadsheets, images, etc.

    # --- Cache --------------------------------------------------------------

    def _cache_file(self, file_id: str) -> Path | None:
        return self.cache_dir / f"{file_id}.json" if self.cache_dir else None

    def _read_cache(self, file_id: str, modified: str) -> str | None:
        """Return cached text if present, current, and same extractor version."""
        path = self._cache_file(file_id)
        if not path or not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except Exception:  # noqa: BLE001 - corrupt cache, just re-fetch
            return None
        if data.get("v") != EXTRACT_VERSION or data.get("modifiedTime") != modified:
            return None
        return data.get("text", "")

    def _write_cache(self, file_id: str, modified: str, text: str) -> None:
        path = self._cache_file(file_id)
        if not path:
            return
        try:
            path.write_text(
                json.dumps(
                    {"v": EXTRACT_VERSION, "modifiedTime": modified, "text": text}
                )
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[drive] cache write failed for {file_id}: {exc}")

    def fetch_all(self) -> list[DriveDoc]:
        """Download and extract text from every supported file (cache-aware)."""
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        docs: list[DriveDoc] = []
        for f in self.list_files():
            source = f.get("path", f["name"])
            modified = f.get("modifiedTime", "")
            text = self._read_cache(f["id"], modified)
            if text is None:
                text = self._extract_text(f)
                self._write_cache(f["id"], modified, text)
            if text.strip():
                docs.append(
                    DriveDoc(
                        file_id=f["id"],
                        name=source,
                        mime_type=f["mimeType"],
                        text=text,
                    )
                )
        return docs
