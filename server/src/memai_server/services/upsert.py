# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from enum import Enum
from uuid import UUID

from ..domain.model import Concept, EngagementLevel, Episode, MemoryType, Procedure
from .ports import (
    DisambiguationEvaluator,
    EmbeddingService,
    MemoryItem,
    MemoryRepository,
    MemorySynthesizer,
)


def _max_engagement(a: EngagementLevel, b: EngagementLevel) -> EngagementLevel:
    return a if a >= b else b


# Defaults match the values documented in CLAUDE.md's "Upsert similarity threshold"
# section — placeholders pending calibration against real usage data. Real values are
# read from [memory] in memai.toml (see infrastructure/config.py) and passed in by callers.
DEFAULT_MERGE_THRESHOLD = 0.93
DEFAULT_DISAMBIGUATE_THRESHOLD = 0.75


class _MergeAction(Enum):
    AUTO_MERGE = "auto_merge"
    DISAMBIGUATE = "disambiguate"
    AUTO_INSERT = "auto_insert"


def _merge_action(similarity: float, merge_threshold: float, disambiguate_threshold: float) -> _MergeAction:
    if similarity >= merge_threshold:
        return _MergeAction.AUTO_MERGE
    if similarity >= disambiguate_threshold:
        return _MergeAction.DISAMBIGUATE
    return _MergeAction.AUTO_INSERT


def _existing_to_merge(
    candidates: list[tuple[float, MemoryItem]],
    candidate: MemoryItem,
    disambiguator: DisambiguationEvaluator,
    merge_threshold: float,
    disambiguate_threshold: float,
) -> MemoryItem | None:
    """Returns the existing record to merge into, or None to insert as new."""
    if not candidates:
        return None
    similarity, best = candidates[0]
    action = _merge_action(similarity, merge_threshold, disambiguate_threshold)
    if action == _MergeAction.AUTO_INSERT:
        return None
    if action == _MergeAction.AUTO_MERGE:
        return best
    return best if disambiguator.is_same(best, candidate) else None


class MemoryUpserter:
    """Shared merge-or-insert pipeline: embed → similarity search → two-tier threshold
    (auto-merge / LLM disambiguation / auto-insert) → LLM synthesis on merge → repository
    upsert. Used by both offline consolidation and the bundle installer — there is
    deliberately no separate insertion path, which is what makes bundle installs
    deduplicate against existing memory and reinstalls idempotent.

    Each upsert_* method mutates the passed item in place (description/summary,
    embedding, engagement, category, id) and returns True when it merged into an
    existing item, False when it inserted a new one.
    """

    def __init__(
        self,
        memory_repo: MemoryRepository,
        embedding_service: EmbeddingService,
        disambiguator: DisambiguationEvaluator,
        synthesizer: MemorySynthesizer,
        merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
        disambiguate_threshold: float = DEFAULT_DISAMBIGUATE_THRESHOLD,
    ) -> None:
        self._memory_repo = memory_repo
        self._embedding_service = embedding_service
        self._disambiguator = disambiguator
        self._synthesizer = synthesizer
        self._merge_threshold = merge_threshold
        self._disambiguate_threshold = disambiguate_threshold

    def upsert_episode(self, episode: Episode) -> bool:
        episode.embedding = self._embedding_service.embed(episode.summary)
        candidates = self._memory_repo.search(episode.embedding, (MemoryType.EPISODE,), top_n=1)
        existing = self._find_existing(candidates, episode)
        if existing is not None:
            episode.summary = self._synthesizer.synthesize_episode(existing.summary, episode.summary)
            episode.embedding = self._embedding_service.embed(episode.summary)
            episode.id = existing.id
        episode.id = self._memory_repo.upsert_episode(episode)
        return existing is not None

    def upsert_concept(self, concept: Concept, persona_id: UUID) -> bool:
        concept.embedding = self._embedding_service.embed(f"{concept.name}: {concept.description}")
        candidates = self._memory_repo.search(
            concept.embedding, (MemoryType.CONCEPT,), top_n=1, persona_id=persona_id,
        )
        existing = self._find_existing(candidates, concept)
        if existing is not None:
            # Exact-duplicate short-circuit: same name + description needs no LLM
            # synthesis or re-embedding — this makes bundle reinstalls near-free.
            if not (existing.name == concept.name and existing.description == concept.description):
                concept.description = self._synthesizer.synthesize_concept(existing, concept.description)
                concept.embedding = self._embedding_service.embed(f"{concept.name}: {concept.description}")
            concept.engagement_level = _max_engagement(existing.engagement_level, concept.engagement_level)
            # An existing category (curated bundle content or an earlier
            # extraction) wins; the new extraction only fills a gap.
            concept.category = existing.category or concept.category
            concept.id = existing.id
        concept.id = self._memory_repo.upsert_concept(concept)
        return existing is not None

    def upsert_procedure(self, procedure: Procedure, persona_id: UUID) -> bool:
        procedure.embedding = self._embedding_service.embed(f"{procedure.name}: {procedure.description}")
        candidates = self._memory_repo.search(
            procedure.embedding, (MemoryType.PROCEDURE,), top_n=1, persona_id=persona_id,
        )
        existing = self._find_existing(candidates, procedure)
        if existing is not None:
            # Same exact-duplicate short-circuit as upsert_concept (steps included:
            # differing steps are new evidence and must go through synthesis).
            if not (
                existing.name == procedure.name
                and existing.description == procedure.description
                and existing.steps == procedure.steps
            ):
                procedure.description, procedure.steps = self._synthesizer.synthesize_procedure(
                    existing, procedure.description, procedure.steps
                )
                procedure.embedding = self._embedding_service.embed(f"{procedure.name}: {procedure.description}")
            procedure.engagement_level = _max_engagement(existing.engagement_level, procedure.engagement_level)
            procedure.category = existing.category or procedure.category
            procedure.id = existing.id
        procedure.id = self._memory_repo.upsert_procedure(procedure)
        return existing is not None

    def _find_existing(self, candidates: list[tuple[float, MemoryItem]], candidate: MemoryItem) -> MemoryItem | None:
        return _existing_to_merge(
            candidates, candidate, self._disambiguator,
            self._merge_threshold, self._disambiguate_threshold,
        )
