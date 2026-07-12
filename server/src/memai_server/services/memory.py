# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from datetime import datetime
from uuid import UUID

from ..domain.model import Concept, EngagementLevel, MemoryBrief, MemoryType
from ..domain.protocols import WorthinessEvaluator
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
    PersonaAssessmentPort,
    PersonaEnrichmentPort,
    UnitOfWork,
    UserRepository,
)
from .upsert import DEFAULT_DISAMBIGUATE_THRESHOLD, DEFAULT_MERGE_THRESHOLD, MemoryUpserter


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
        unit_of_work: UnitOfWork,
        user_repo: UserRepository,
        assessment_strategies: dict[UUID, PersonaAssessmentPort] | None = None,
        merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
        disambiguate_threshold: float = DEFAULT_DISAMBIGUATE_THRESHOLD,
    ) -> None:
        self._conversation_repo = conversation_repo
        self._memory_repo = memory_repo
        self._extractor = extractor
        self._worthiness_evaluator = worthiness_evaluator
        self._unit_of_work = unit_of_work
        self._user_repo = user_repo
        # persona_id -> assessment strategy; personas without one (e.g. GA) are skipped.
        self._assessment_strategies = assessment_strategies or {}
        # Shared merge-or-insert pipeline, also used by InstallPersonaBundle — bundle
        # content and extracted content deduplicate through the exact same path.
        self._upserter = MemoryUpserter(
            memory_repo, embedding_service, disambiguator, synthesizer,
            merge_threshold, disambiguate_threshold,
        )

    def execute(self) -> int:
        """Synchronous by design: every step here (LLM extraction/evaluation/synthesis,
        embedding, DB upserts) is a blocking call with no real `await` point. Callers that
        run this from an asyncio event loop (see `server.py`) must dispatch it via
        `asyncio.to_thread` so it doesn't block other connections for the run's duration."""
        conversations = self._conversation_repo.get_unconsolidated()
        user = self._user_repo.get()
        primary_language = user.primary_language if user else None
        processed = 0
        for conversation in conversations:
            # One transaction per conversation: if anything below raises, none of this
            # conversation's episodes/concepts/procedures/consolidated-flag are committed,
            # so it's safely reprocessed in full on the next run.
            with self._unit_of_work:
                worthy = self._worthiness_evaluator.evaluate(conversation)
                extraction = self._extractor.extract(conversation, primary_language)

                # Episodes require a worthy conversation — trivial exchanges shouldn't
                # generate episodic memories. Concepts and procedures are extracted
                # unconditionally: knowledge is worth keeping regardless of conversation quality.
                if worthy:
                    for episode in extraction.episodes:
                        self._upserter.upsert_episode(episode)

                for concept in extraction.concepts:
                    self._upserter.upsert_concept(concept, conversation.persona_id)

                for procedure in extraction.procedures:
                    self._upserter.upsert_procedure(procedure, conversation.persona_id)

                # Persona assessment hook — runs AFTER upsert so newly inserted items have
                # ids and their first exposure is assessable. The returned persona_state
                # dicts are persisted byte-for-byte; generic code never reads inside them.
                strategy = self._assessment_strategies.get(conversation.persona_id)
                if strategy is not None:
                    touched: list[MemoryItem] = [*extraction.concepts, *extraction.procedures]
                    if touched:
                        for assessment in strategy.assess_items(conversation.persona_id, conversation, touched):
                            self._memory_repo.update_persona_state(
                                assessment.memory_type, assessment.item_id, assessment.persona_state
                            )

                conversation.mark_consolidated(worthiness=worthy, summary=None)
                self._conversation_repo.save_consolidation(conversation)
            processed += 1

        return processed


class EnrichMemory:
    """Offline dispatch of the optional PersonaEnrichmentPort — runs after
    consolidation (so fresh persona_state is visible to the strategies), feeding
    proposed drafts through the same upsert pipeline as extraction and bundle
    install. Which items matter for exclusion is persona-specific knowledge computed
    inside each strategy; the upsert-merge dedup is the safety net either way."""

    def __init__(
        self,
        memory_repo: MemoryRepository,
        embedding_service: EmbeddingService,
        disambiguator: DisambiguationEvaluator,
        synthesizer: MemorySynthesizer,
        unit_of_work: UnitOfWork,
        enrichment_strategies: dict[UUID, PersonaEnrichmentPort] | None = None,
        merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
        disambiguate_threshold: float = DEFAULT_DISAMBIGUATE_THRESHOLD,
    ) -> None:
        # persona_id -> enrichment strategy; personas without one (e.g. GA) are skipped.
        self._enrichment_strategies = enrichment_strategies or {}
        self._unit_of_work = unit_of_work
        self._upserter = MemoryUpserter(
            memory_repo, embedding_service, disambiguator, synthesizer,
            merge_threshold, disambiguate_threshold,
        )

    def execute(self) -> int:
        """Synchronous by design, like ConsolidateMemory — callers on an event loop
        dispatch it via asyncio.to_thread. Returns the number of drafts processed."""
        processed = 0
        for persona_id, strategy in self._enrichment_strategies.items():
            drafts = list(strategy.propose_items(persona_id))
            if not drafts:
                continue
            # One transaction per persona's proposal batch, mirroring the
            # per-conversation / per-lesson granularity elsewhere.
            with self._unit_of_work:
                for draft in drafts:
                    # A proposal can never claim the user knows the item (same rule as
                    # bundle install); on merge the upserter's max-engagement keeps any
                    # higher level already earned.
                    draft.engagement_level = EngagementLevel.UNSEEN
                    if isinstance(draft, Concept):
                        self._upserter.upsert_concept(draft, persona_id)
                    else:
                        self._upserter.upsert_procedure(draft, persona_id)
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
