# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from datetime import datetime
from uuid import UUID

from ..domain.model import Concept, Conversation, EngagementLevel, MemoryBrief, MemoryType, Speaker
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

# Calibration placeholders (FR-307) — below this floor, a conversation is too thin to
# be worth even asking the LLM about: skips worthiness evaluation AND extraction
# entirely (concepts included, not just episodes), purely for cost control. Counts only
# the USER's own turns/words — assistant chatter (GA's own boilerplate, stock
# capability descriptions) must never inflate this past the floor, since that's exactly
# what the 2026-07-18 review found padding the DB with noise.
DEFAULT_MIN_USER_TURNS = 2
DEFAULT_MIN_USER_WORDS = 40


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
        min_user_turns: int = DEFAULT_MIN_USER_TURNS,
        min_user_words: int = DEFAULT_MIN_USER_WORDS,
    ) -> None:
        self._conversation_repo = conversation_repo
        self._memory_repo = memory_repo
        self._extractor = extractor
        self._worthiness_evaluator = worthiness_evaluator
        self._unit_of_work = unit_of_work
        self._user_repo = user_repo
        self._min_user_turns = min_user_turns
        self._min_user_words = min_user_words
        # persona_id -> assessment strategy; personas without one (e.g. GA) are skipped.
        self._assessment_strategies = assessment_strategies or {}
        # Shared merge-or-insert pipeline, also used by InstallPersonaBundle — bundle
        # content and extracted content deduplicate through the exact same path.
        self._upserter = MemoryUpserter(
            memory_repo, embedding_service, disambiguator, synthesizer,
            merge_threshold, disambiguate_threshold,
        )

    def _meets_extraction_floor(self, conversation: Conversation) -> bool:
        user_turns = [t for t in conversation.turns if t.speaker is Speaker.USER]
        if len(user_turns) < self._min_user_turns:
            return False
        return sum(len(t.content.split()) for t in user_turns) >= self._min_user_words

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
            # conversation's episodes/concepts/consolidated-flag are committed, so it's
            # safely reprocessed in full on the next run.
            with self._unit_of_work:
                if not self._meets_extraction_floor(conversation):
                    # Too thin to be worth even asking the LLM — skip worthiness AND
                    # extraction entirely (cost control, FR-307).
                    conversation.mark_consolidated(worthiness=False, summary=None)
                    self._conversation_repo.save_consolidation(conversation)
                    processed += 1
                    continue

                worthy = self._worthiness_evaluator.evaluate(conversation)
                # A persona with its own registered assessment strategy (today, only the
                # language tutor) owns lesson practice end to end — a lesson's
                # role-play/drills are not real events, so episodes are never even
                # requested for it (FR-407/504). Concepts are a different matter: see
                # below, they're gated by origin/engagement, not by persona.
                strategy = self._assessment_strategies.get(conversation.persona_id)
                extraction = self._extractor.extract(
                    conversation, primary_language, extract_episodes=strategy is None,
                )

                # Episodes require a worthy conversation — trivial exchanges shouldn't
                # generate episodic memories.
                if worthy:
                    for episode in extraction.episodes:
                        self._upserter.upsert_episode(episode)

                # Concepts are gated independently of `worthy`: origin-awareness inside
                # upsert_concept protects curated (authored) content from being rewritten,
                # and a brand-new organic concept additionally needs real user engagement
                # (user_turns) — see MemoryUpserter's docstring. This applies uniformly to
                # every persona, including strategy-owning ones: a user going off-curriculum
                # mid-lesson to discuss something real is genuine signal (FR-407), unlike a
                # lesson drill itself, which never contains any 2nd-person-real content to
                # begin with.
                user_turns = [t.content for t in conversation.turns if t.speaker is Speaker.USER]
                touched: list[MemoryItem] = []
                for concept in extraction.concepts:
                    concept.origin = "organic"
                    self._upserter.upsert_concept(concept, conversation.persona_id, user_turns=user_turns)
                    if concept.id is not None:  # None means discarded (insufficient engagement, no match)
                        touched.append(concept)

                # Persona assessment hook — runs AFTER upsert so newly inserted items have
                # ids and their first exposure is assessable. The returned persona_state
                # dicts are persisted byte-for-byte; generic code never reads inside them.
                if strategy is not None and touched:
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
                        # Curated/curriculum content: origin="authored" is what protects
                        # it from being rewritten by a later live-conversation extraction
                        # (MemoryUpserter.upsert_concept's authored-protection check).
                        draft.origin = "authored"
                        self._upserter.upsert_concept(draft, persona_id)
                    else:
                        # Procedures are always authored (FR-307) — a match here is
                        # curated content, never edited by a later proposal batch.
                        self._upserter.upsert_procedure(draft, persona_id, update_description=False)
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
