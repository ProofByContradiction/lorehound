"""Google Drive access: list a folder, download files, extract plain text.

Authenticates with a *service account* (no interactive OAuth). You create the
service account once, share your rules folder with its email, and provide the
key either as a file path (GOOGLE_CREDENTIALS_FILE) or as the raw JSON
(GOOGLE_CREDENTIALS_JSON, handy for hosts that only offer env vars). See README.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

# Read-only access is all we need.
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Google-native types we know how to export to plain text.
GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_FOLDER = "application/vnd.google-apps.folder"


@dataclass
class DriveDoc:
    file_id: str
    name: str
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
    ) -> None:
        if not folder_id:
            raise DriveNotConfigured("No DRIVE_FOLDER_ID configured.")
        self.folder_id = folder_id
        self._credentials_file = credentials_file
        self._credentials_json = credentials_json
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

    def list_files(self) -> list[dict]:
        """List non-folder files directly inside the configured folder."""
        query = (
            f"'{self.folder_id}' in parents and trashed = false "
            f"and mimeType != '{GOOGLE_FOLDER}'"
        )
        files: list[dict] = []
        page_token = None
        while True:
            resp = (
                self.service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType)",
                    pageToken=page_token,
                    pageSize=100,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

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
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            # Tag each page so we can cite "p. N" in search results.
            pages.append(f"[[page {i}]]\n{text}")
        return "\n\n".join(pages)

    def fetch_all(self) -> list[DriveDoc]:
        """Download and extract text from every supported file in the folder."""
        docs: list[DriveDoc] = []
        for f in self.list_files():
            mime = f["mimeType"]
            name = f["name"]
            try:
                if mime == GOOGLE_DOC:
                    text = self._export_google_doc(f["id"])
                elif mime == "application/pdf" or name.lower().endswith(".pdf"):
                    text = self._extract_pdf(self._download_bytes(f["id"]))
                elif mime.startswith("text/") or name.lower().endswith(
                    (".txt", ".md")
                ):
                    text = self._download_bytes(f["id"]).decode(
                        "utf-8", errors="replace"
                    )
                else:
                    # Skip spreadsheets, images, etc. for now.
                    continue
            except Exception as exc:  # noqa: BLE001 - report and keep going
                text = ""
                print(f"[drive] failed to read {name}: {exc}")
            if text.strip():
                docs.append(
                    DriveDoc(
                        file_id=f["id"], name=name, mime_type=mime, text=text
                    )
                )
        return docs
