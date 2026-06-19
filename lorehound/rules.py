"""Glue between Google Drive and the search index.

Owns the cached rules corpus: pulls documents from Drive, splits them into
cited chunks (PDF page markers become "p. N" locators), and answers searches.
"""

from __future__ import annotations

import re

from .drive_client import DriveClient
from .search_index import Chunk, SearchHit, SearchIndex, chunk_text

_PAGE_MARKER = re.compile(r"\[\[page (\d+)\]\]")


def _chunks_for_doc(name: str, text: str) -> list[Chunk]:
    """Chunk one document, preserving PDF page numbers as locators when present."""
    if _PAGE_MARKER.search(text):
        chunks: list[Chunk] = []
        # Split on page markers, keeping the page number for each segment.
        parts = _PAGE_MARKER.split(text)
        # parts = [pre, page_no, body, page_no, body, ...]; ignore leading pre.
        for i in range(1, len(parts) - 1, 2):
            page_no = parts[i]
            body = parts[i + 1]
            chunks.extend(chunk_text(name, body, locator=f"p. {page_no}"))
        return chunks
    return chunk_text(name, text)


class RulesService:
    def __init__(self, drive: DriveClient | None) -> None:
        self.drive = drive
        self.index = SearchIndex()

    @property
    def ready(self) -> bool:
        return not self.index.is_empty

    def refresh(self) -> dict:
        """(Re)download from Drive and rebuild the index. Returns a summary."""
        if self.drive is None:
            raise RuntimeError("Google Drive is not configured.")
        docs = self.drive.fetch_all()
        chunks: list[Chunk] = []
        for doc in docs:
            chunks.extend(_chunks_for_doc(doc.name, doc.text))
        self.index.build(chunks)
        return {
            "documents": len(docs),
            "chunks": len(chunks),
            "sources": [d.name for d in docs],
        }

    def search(self, query: str, top_k: int = 3) -> list[SearchHit]:
        return self.index.search(query, top_k=top_k)
