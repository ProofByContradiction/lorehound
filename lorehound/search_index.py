"""Text chunking + a small BM25 search index.

Deliberately dependency-free (no numpy) so it stays light on free hosting tiers
and avoids version friction on bleeding-edge Python. Good enough for keyword
rules lookup; we can swap in semantic/embedding search later behind the same
``search()`` interface.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


@dataclass
class Chunk:
    source: str          # document title / filename
    locator: str         # e.g. "p. 42" or "section 3"
    text: str
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = _tokenize(self.text)


@dataclass
class SearchHit:
    chunk: Chunk
    score: float


def chunk_text(
    source: str, text: str, locator: str = "", target_words: int = 120
) -> list[Chunk]:
    """Split a document's text into paragraph-ish chunks of ~target_words."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_words = 0

    def flush() -> None:
        nonlocal buf, buf_words
        if buf:
            chunks.append(Chunk(source=source, locator=locator, text="\n\n".join(buf)))
            buf = []
            buf_words = 0

    for para in paragraphs:
        words = len(para.split())
        if buf_words + words > target_words and buf:
            flush()
        buf.append(para)
        buf_words += words
    flush()
    return chunks


class SearchIndex:
    """In-memory BM25 index over a list of chunks."""

    K1 = 1.5
    B = 0.75

    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self._doc_freq: Counter[str] = Counter()
        self._avg_len: float = 0.0
        self._sources: set[str] = set()

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    @property
    def sources(self) -> list[str]:
        return sorted(self._sources)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    def build(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self._sources = {c.source for c in chunks}
        self._doc_freq = Counter()
        for c in chunks:
            for term in set(c.tokens):
                self._doc_freq[term] += 1
        total_len = sum(len(c.tokens) for c in chunks)
        self._avg_len = (total_len / len(chunks)) if chunks else 0.0

    def search(self, query: str, top_k: int = 3) -> list[SearchHit]:
        if self.is_empty:
            return []
        q_terms = _tokenize(query)
        if not q_terms:
            return []

        n = len(self.chunks)
        scored: list[SearchHit] = []
        for chunk in self.chunks:
            freqs = Counter(chunk.tokens)
            dl = len(chunk.tokens) or 1
            score = 0.0
            for term in q_terms:
                tf = freqs.get(term, 0)
                if tf == 0:
                    continue
                df = self._doc_freq.get(term, 0) or 1
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                denom = tf + self.K1 * (
                    1 - self.B + self.B * dl / (self._avg_len or 1)
                )
                score += idf * (tf * (self.K1 + 1)) / denom
            if score > 0:
                scored.append(SearchHit(chunk=chunk, score=score))

        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]
