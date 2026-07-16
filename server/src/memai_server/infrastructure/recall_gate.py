# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations


class DefaultRecallGate:
    """The RecallGate every persona gets unless it registers its own (FR-309/TR-314).
    Two independent short-circuits, in order:

    1. should_embed: skip a trivial short utterance entirely (a bare "yes"/"ok"/
       "thanks") — not worth an embedding call or a DB round trip. `min_words` is a
       word-count floor, not a character count, since word count tracks "is there
       enough content here to search for" far better across languages than length.
    2. should_search: given an embedding was computed, skip the DB round trip when
       it's nearly identical to *any* prior search this session (not just the most
       recent one) — the cached results from that search are still the best available
       answer, so paying for another lookup buys nothing. Comparing against the whole
       history rather than only the last entry matters because nothing new can enter
       long-term memory mid-session (INV-1): the searchable set is frozen for the
       whole conversation, so a repeat of any earlier query, not only the immediately
       preceding one, would return the same thing again. `dedup_threshold` reuses this
       project's existing 0.93 "treat as the same thing" bar (ConsolidateMemory's
       merge_threshold, [memory].merge_threshold) rather than inventing a new
       placeholder constant."""

    def __init__(self, min_words: int = 3, dedup_threshold: float = 0.93) -> None:
        self._min_words = min_words
        self._dedup_threshold = dedup_threshold

    def should_embed(self, text: str) -> bool:
        return len(text.split()) >= self._min_words

    def should_search(self, max_similarity_to_prior_searches: float | None) -> bool:
        if max_similarity_to_prior_searches is None:
            return True
        return max_similarity_to_prior_searches < self._dedup_threshold
