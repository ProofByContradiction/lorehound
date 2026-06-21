"""BM25 search index over heading-aware, categorized chunks.

Dependency-free (no numpy) so it stays light on free hosting tiers. Chunks carry
game / book / category / section metadata so searches can be scoped and filtered.
Chunking itself lives in rules.py (it's format-aware); this module just indexes.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


@dataclass
class Chunk:
    game: str            # e.g. "Twilight: 2000"
    source: str          # book file, e.g. "T2K Lore.pdf"
    category: str        # "rules" | "items" | "vehicles"
    section: str         # heading breadcrumb, e.g. "Combat & Damage › Explosions › Sniper"
    locator: str         # "p. 69"
    text: str
    rows: list[list[str]] = field(default_factory=list)  # cell grid (tables only)
    tokens: list[str] = field(default_factory=list)
    heading_terms: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.tokens:
            # Index the heading breadcrumb alongside the body so keyword search
            # matches section terms even when the body never repeats them.
            head = f"{self.section} " if self.section else ""
            self.tokens = tokenize(head + self.text)
        if not self.heading_terms:
            # Terms from the breadcrumb (chapter › section › entry) earn an extra
            # boost at search time, so the chunk that *is about* a topic outranks
            # chunks that merely mention the word in passing.
            self.heading_terms = set(tokenize(self.section))


@dataclass
class SearchHit:
    chunk: Chunk
    score: float


class SearchIndex:
    K1 = 1.5
    B = 0.75
    # Extra weight for query terms found in a chunk's heading breadcrumb (e.g. the
    # specialty / gear / skill name), so the defining entry ranks above passing
    # mentions of the same word elsewhere.
    HEADING_BOOST = 2.0
    # Once there's a clear top hit, drop results scoring below this fraction of it
    # — far-weaker matches are passing mentions, not rules about the query.
    REL_CUTOFF = 0.40

    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self._doc_freq: Counter[str] = Counter()
        self._avg_len: float = 0.0
        self._files_by_game: dict[str, set[str]] = {}

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    @property
    def games(self) -> list[str]:
        return sorted(self._files_by_game)

    @property
    def files_by_game(self) -> dict[str, list[str]]:
        return {g: sorted(files) for g, files in self._files_by_game.items()}

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    def build(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self._files_by_game = {}
        for c in chunks:
            self._files_by_game.setdefault(c.game, set()).add(c.source)
        self._doc_freq = Counter()
        for c in chunks:
            for term in set(c.tokens):
                self._doc_freq[term] += 1
        total_len = sum(len(c.tokens) for c in chunks)
        self._avg_len = (total_len / len(chunks)) if chunks else 0.0

    def search(
        self,
        query: str,
        top_k: int = 5,
        game: str | None = None,
        book: str | None = None,
        category: str | None = None,
    ) -> list[SearchHit]:
        if self.is_empty:
            return []
        q_terms = tokenize(query)
        if not q_terms:
            return []

        # Filter candidates by scope; idf still reflects the whole corpus.
        candidates = self.chunks
        if game:
            candidates = [c for c in candidates if c.game == game]
        if book:
            candidates = [c for c in candidates if c.source == book]
        if category:
            candidates = [c for c in candidates if c.category == category]
        if not candidates:
            return []

        n = len(self.chunks)
        scored: list[SearchHit] = []
        for chunk in candidates:
            freqs = Counter(chunk.tokens)
            dl = len(chunk.tokens) or 1
            score = 0.0
            for term in q_terms:
                df = self._doc_freq.get(term, 0) or 1
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                tf = freqs.get(term, 0)
                if tf:
                    denom = tf + self.K1 * (
                        1 - self.B + self.B * dl / (self._avg_len or 1)
                    )
                    score += idf * (tf * (self.K1 + 1)) / denom
                # Breadcrumb match: reward the chunk whose section/entry name is
                # exactly what the user searched for.
                if term in chunk.heading_terms:
                    score += self.HEADING_BOOST * idf
            if score > 0:
                scored.append(SearchHit(chunk=chunk, score=score))

        scored.sort(key=lambda h: h.score, reverse=True)
        # Drop the weak tail: passing mentions that score far below the clear top
        # hit are noise, not rules about the query.
        if scored:
            cutoff = scored[0].score * self.REL_CUTOFF
            scored = [h for h in scored if h.score >= cutoff]
        return scored[:top_k]
