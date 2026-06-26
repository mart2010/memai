# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from datetime import datetime
from enum import Enum

from ..domain.model import EngagementLevel, MemoryBrief, MemoryType
from .ports import (
    ConsolidationExtractor,
    ConversationRepository,
    DisambiguationEvaluator,
    EmbeddingService,
    LLMService,
    MemoryBriefRepository,
    MemoryItem,
    MemoryRepository,
    MemorySynthesizer,
    Message,
    WorthinessEvaluator,
)

def _max_engagement(a: EngagementLevel, b: EngagementLevel) -> EngagementLevel:
    return a if a >= b else b

_MERGE_THRESHOLD = 0.93
_DISAMBIGUATE_THRESHOLD = 0.75


class _MergeAction(Enum):
    AUTO_MERGE = "auto_merge"
    DISAMBIGUATE = "disambiguate"
    AUTO_INSERT = "auto_insert"


def _merge_action(similarity: float) -> _MergeAction:
    if similarity >= _MERGE_THRESHOLD:
        return _MergeAction.AUTO_MERGE
    if similarity >= _DISAMBIGUATE_THRESHOLD:
        return _MergeAction.DISAMBIGUATE
    return _MergeAction.AUTO_INSERT


def _existing_to_merge(
    candidates: list[tuple[float, MemoryItem]],
    candidate: MemoryItem,
    disambiguator: DisambiguationEvaluator,
) -> MemoryItem | None:
    """Returns the existing record to merge into, or None to insert as new."""
    if not candidates:
        return None
    similarity, best = candidates[0]
    action = _merge_action(similarity)
    if action == _MergeAction.AUTO_INSERT:
        return None
    if action == _MergeAction.AUTO_MERGE:
        return best
    return best if disambiguator.is_same(best, candidate) else None


class TriggerRecall:
    def __init__(self, embedding_service: EmbeddingService, memory_repo: MemoryRepository) -> None:
        self._embedding_service = embedding_service
        self._memory_repo = memory_repo

    def execute(self, query: str, memory_types: tuple[MemoryType, ...], top_n: int = 5) -> list[MemoryItem]:
        embedding = self._embedding_service.embed(query)
        return [item for _, item in self._memory_repo.search(embedding, memory_types, top_n)]


class ConsolidateMemory:
    def __init__(
        self,
        conversation_repo: ConversationRepository,
        memory_repo: MemoryRepository,
        embedding_service: EmbeddingService,
        extractor: ConsolidationExtractor,
        worthiness_evaluator: WorthinessEvaluator,
        disambiguator: DisambiguationEvaluator,
        synthesizer: MemorySynthesizer,
    ) -> None:
        self._conversation_repo = conversation_repo
        self._memory_repo = memory_repo
        self._embedding_service = embedding_service
        self._extractor = extractor
        self._worthiness_evaluator = worthiness_evaluator
        self._disambiguator = disambiguator
        self._synthesizer = synthesizer

    async def execute(self) -> int:
        conversations = self._conversation_repo.get_unconsolidated()
        processed = 0
        for conversation in conversations:
            worthy = self._worthiness_evaluator.evaluate(conversation)
            extraction = self._extractor.extract(conversation)

            # Episodes require a worthy conversation — trivial exchanges shouldn't
            # generate episodic memories. Concepts and procedures are extracted
            # unconditionally: knowledge is worth keeping regardless of conversation quality.
            if worthy:
                for episode in extraction.episodes:
                    episode.embedding = self._embedding_service.embed(episode.summary)
                    candidates = self._memory_repo.search(episode.embedding, (MemoryType.EPISODE,), top_n=1)
                    existing = _existing_to_merge(candidates, episode, self._disambiguator)
                    if existing is not None:
                        episode.summary = self._synthesizer.synthesize_episode(existing.summary, episode.summary)
                        episode.embedding = self._embedding_service.embed(episode.summary)
                        episode.id = existing.id
                    episode.id = self._memory_repo.upsert_episode(episode)

            for concept in extraction.concepts:
                concept.embedding = self._embedding_service.embed(f"{concept.name}: {concept.description}")
                candidates = self._memory_repo.search(
                    concept.embedding, (MemoryType.CONCEPT,), top_n=1,
                    persona_id=conversation.persona_snapshot.id,
                )
                existing = _existing_to_merge(candidates, concept, self._disambiguator)
                if existing is not None:
                    concept.description = self._synthesizer.synthesize_concept(existing, concept.description)
                    concept.embedding = self._embedding_service.embed(f"{concept.name}: {concept.description}")
                    concept.engagement_level = _max_engagement(existing.engagement_level, concept.engagement_level)
                    concept.id = existing.id
                concept.id = self._memory_repo.upsert_concept(concept)

            for procedure in extraction.procedures:
                procedure.embedding = self._embedding_service.embed(f"{procedure.name}: {procedure.description}")
                candidates = self._memory_repo.search(
                    procedure.embedding, (MemoryType.PROCEDURE,), top_n=1,
                    persona_id=conversation.persona_snapshot.id,
                )
                existing = _existing_to_merge(candidates, procedure, self._disambiguator)
                if existing is not None:
                    procedure.description, procedure.steps = self._synthesizer.synthesize_procedure(
                        existing, procedure.description, procedure.steps
                    )
                    procedure.embedding = self._embedding_service.embed(f"{procedure.name}: {procedure.description}")
                    procedure.engagement_level = _max_engagement(existing.engagement_level, procedure.engagement_level)
                    procedure.id = existing.id
                procedure.id = self._memory_repo.upsert_procedure(procedure)

            conversation.mark_consolidated(worthiness=worthy, summary=None)
            self._conversation_repo.save_consolidation(conversation)
            processed += 1

        return processed


class GenerateMemoryBrief:
    def __init__(self, llm: LLMService, memory_brief_repo: MemoryBriefRepository) -> None:
        self._llm = llm
        self._memory_brief_repo = memory_brief_repo

    async def execute(self, generated_at: datetime) -> MemoryBrief:
        tokens: list[str] = []
        async for token in self._llm.complete(
            messages=[Message(role="user", content="Generate a concise brief of what you know about the user.")],
            system_prompt="You are a memory manager. Summarise the user's profile, recurring themes, and key knowledge concisely.",
        ):
            tokens.append(token)
        brief = MemoryBrief(content="".join(tokens).strip(), created_at=generated_at, updated_at=generated_at)
        self._memory_brief_repo.save(brief)
        return brief
