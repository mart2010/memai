# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import math
from datetime import datetime

from ..domain.model import MemoryBrief, MemoryType
from ..domain.protocols import WorthinessEvaluator
from .ports import (
    ConsolidationExtractor,
    EmbeddingService,
    LLMService,
    MemoryBriefRepository,
    MemoryItem,
    MemoryRepository,
    ConversationRepository,
    Message,
)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _should_merge(candidates: list[MemoryItem], embedding: list[float], threshold: float) -> bool:
    if not candidates:
        return False
    first = candidates[0]
    if first.embedding is None:
        return False
    return _cosine_similarity(embedding, first.embedding) >= threshold


class TriggerRecall:
    def __init__(self, embedding_service: EmbeddingService, memory_repo: MemoryRepository) -> None:
        self._embedding_service = embedding_service
        self._memory_repo = memory_repo

    def execute(self, query: str, memory_types: tuple[MemoryType, ...], top_n: int = 5) -> list[MemoryItem]:
        embedding = self._embedding_service.embed(query)
        return self._memory_repo.search(embedding, memory_types, top_n)


class RunConsolidation:
    def __init__(
        self,
        conversation_repo: ConversationRepository,
        memory_repo: MemoryRepository,
        embedding_service: EmbeddingService,
        extractor: ConsolidationExtractor,
        worthiness_evaluator: WorthinessEvaluator,
        similarity_threshold: float = 0.85,
    ) -> None:
        self._conversation_repo = conversation_repo
        self._memory_repo = memory_repo
        self._embedding_service = embedding_service
        self._extractor = extractor
        self._worthiness_evaluator = worthiness_evaluator
        self._threshold = similarity_threshold

    async def execute(self) -> int:
        conversations = self._conversation_repo.get_unconsolidated()
        processed = 0
        for conversation in conversations:
            worthy = self._worthiness_evaluator.evaluate(conversation)
            extraction = self._extractor.extract(conversation)

            if worthy:
                for episode in extraction.episodes:
                    episode.embedding = self._embedding_service.embed(episode.summary)
                    candidates = self._memory_repo.search(episode.embedding, (MemoryType.EPISODE,), top_n=1)
                    if not _should_merge(candidates, episode.embedding, self._threshold):
                        episode.id = self._memory_repo.upsert_episode(episode)

            for concept in extraction.concepts:
                concept.embedding = self._embedding_service.embed(f"{concept.name}: {concept.description}")
                candidates = self._memory_repo.search(
                    concept.embedding, (MemoryType.CONCEPT,), top_n=1,
                    persona_id=conversation.persona_snapshot.id,
                )
                if not _should_merge(candidates, concept.embedding, self._threshold):
                    concept.id = self._memory_repo.upsert_concept(concept)

            for procedure in extraction.procedures:
                procedure.embedding = self._embedding_service.embed(f"{procedure.name}: {procedure.description}")
                candidates = self._memory_repo.search(
                    procedure.embedding, (MemoryType.PROCEDURE,), top_n=1,
                    persona_id=conversation.persona_snapshot.id,
                )
                if not _should_merge(candidates, procedure.embedding, self._threshold):
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
        brief = MemoryBrief(content="".join(tokens).strip(), generated_at=generated_at)
        self._memory_brief_repo.save(brief)
        return brief
