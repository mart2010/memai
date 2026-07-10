from datetime import datetime, UTC
from uuid import UUID, uuid4

from memai_server.domain.model import (
    Concept,
    Conversation,
    EngagementLevel,
    Episode,
    GENERAL_ASSISTANT_ID,
    Language,
    MemoryType,
    Speaker,
    Turn,
    User,
)
from memai_server.services.memory import ConsolidateMemory
from memai_server.services.ports import ExtractionResult, ItemAssessment

from tests.fakes.fakes import (
    FakeConsolidationExtractor,
    FakeConversationRepository,
    FakeDisambiguationEvaluator,
    FakeEmbeddingService,
    FakeMemoryRepository,
    FakeMemorySynthesizer,
    FakePersonaAssessmentPort,
    FakeUnitOfWork,
    FakeUserRepository,
    FakeWorthinessEvaluator,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _make_consolidation(
    worthy: bool = True,
    extraction: ExtractionResult | None = None,
    extractor: FakeConsolidationExtractor | None = None,
    user: User | None = None,
    assessment_strategies: dict | None = None,
    disambiguator: FakeDisambiguationEvaluator | None = None,
    memory_repo: FakeMemoryRepository | None = None,
) -> tuple[ConsolidateMemory, FakeConversationRepository, FakeMemoryRepository]:
    conversation_repo = FakeConversationRepository()
    memory_repo = memory_repo or FakeMemoryRepository()
    use_case = ConsolidateMemory(
        conversation_repo=conversation_repo,
        memory_repo=memory_repo,
        embedding_service=FakeEmbeddingService(),
        extractor=extractor or FakeConsolidationExtractor(result=extraction),
        worthiness_evaluator=FakeWorthinessEvaluator(worthy=worthy),
        disambiguator=disambiguator or FakeDisambiguationEvaluator(),
        synthesizer=FakeMemorySynthesizer(),
        unit_of_work=FakeUnitOfWork(),
        user_repo=FakeUserRepository(user or User(id=uuid4(), primary_language=Language("en"))),
        assessment_strategies=assessment_strategies,
    )
    return use_case, conversation_repo, memory_repo


_DUMMY_SESSION = UUID("00000000-0000-0000-0000-000000000001")


def _seed_ended_conversation(conversation_repo: FakeConversationRepository) -> None:
    conv = Conversation(
        id=None,
        started_at=_now(),
        persona_id=GENERAL_ASSISTANT_ID,
    )
    conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content="hello"))
    conv.end(ended_at=_now())
    conv.id = conversation_repo.save_new(conv, session_id=_DUMMY_SESSION)


class TestRunConsolidation:
    def test_worthy_conversation_produces_episode(self):
        episode = Episode(id=None, summary="Discussed Python.", happened_at=_now(), origin_conversation_id=1)
        extraction = ExtractionResult(episodes=[episode], concepts=[], procedures=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=True, extraction=extraction)
        _seed_ended_conversation(conversation_repo)

        count = use_case.execute()

        assert count == 1
        assert len(memory_repo.episodes) == 1

    def test_unworthy_conversation_skips_episodes(self):
        episode = Episode(id=None, summary="Short chat.", happened_at=_now(), origin_conversation_id=1)
        extraction = ExtractionResult(episodes=[episode], concepts=[], procedures=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=False, extraction=extraction)
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert len(memory_repo.episodes) == 0

    def test_concepts_extracted_regardless_of_worthiness(self):
        concept = Concept(id=None, persona_id=GENERAL_ASSISTANT_ID, name="Recursion", description="A function calling itself.", language=Language("en"))
        extraction = ExtractionResult(episodes=[], concepts=[concept], procedures=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=False, extraction=extraction)
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert len(memory_repo.concepts) == 1

    def test_conversation_marked_consolidated(self):
        use_case, conversation_repo, _ = _make_consolidation()
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert all(r.consolidated for r in conversation_repo._records.values())

    def test_already_consolidated_conversations_skipped_on_rerun(self):
        use_case, conversation_repo, _ = _make_consolidation()
        _seed_ended_conversation(conversation_repo)

        use_case.execute()
        count2 = use_case.execute()

        assert count2 == 0

    def test_extractor_receives_user_primary_language(self):
        extractor = FakeConsolidationExtractor()
        use_case, conversation_repo, _ = _make_consolidation(
            extractor=extractor,
            user=User(id=uuid4(), primary_language=Language("fr")),
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert extractor.primary_languages == [Language("fr")]


class _CannedSearchMemoryRepository(FakeMemoryRepository):
    """Returns a fixed similarity-search result so merge paths can be exercised."""

    def __init__(self, results) -> None:
        super().__init__()
        self._results = results

    def search(self, embedding, memory_types, top_n, persona_id=None):
        return self._results


class TestCategoryMergeRule:
    def test_existing_category_wins_on_merge(self):
        existing = Concept(
            id=42, persona_id=GENERAL_ASSISTANT_ID, name="ser vs estar",
            description="Curated pair.", language=Language("es"), category="contrast_pair",
        )
        candidate = Concept(
            id=None, persona_id=GENERAL_ASSISTANT_ID, name="ser vs estar",
            description="New evidence.", language=Language("es"), category="verb",
        )
        memory_repo = _CannedSearchMemoryRepository([(0.95, existing)])  # auto-merge band
        extraction = ExtractionResult(episodes=[], concepts=[candidate], procedures=[])
        use_case, conversation_repo, _ = _make_consolidation(extraction=extraction, memory_repo=memory_repo)
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert candidate.category == "contrast_pair"
        assert candidate.id == 42

    def test_new_category_fills_gap_when_existing_has_none(self):
        existing = Concept(
            id=42, persona_id=GENERAL_ASSISTANT_ID, name="comer",
            description="To eat.", language=Language("es"), category=None,
        )
        candidate = Concept(
            id=None, persona_id=GENERAL_ASSISTANT_ID, name="comer",
            description="To eat, richer.", language=Language("es"), category="verb",
        )
        memory_repo = _CannedSearchMemoryRepository([(0.95, existing)])
        extraction = ExtractionResult(episodes=[], concepts=[candidate], procedures=[])
        use_case, conversation_repo, _ = _make_consolidation(extraction=extraction, memory_repo=memory_repo)
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert candidate.category == "verb"


class TestAssessmentHook:
    def _extraction(self) -> ExtractionResult:
        concept = Concept(
            id=None, persona_id=GENERAL_ASSISTANT_ID, name="Recursion",
            description="A function calling itself.", language=Language("en"),
        )
        return ExtractionResult(episodes=[], concepts=[concept], procedures=[])

    def test_assessment_dispatched_after_upsert_with_ids(self):
        strategy = FakePersonaAssessmentPort()
        use_case, conversation_repo, _ = _make_consolidation(
            extraction=self._extraction(),
            assessment_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert len(strategy.calls) == 1
        persona_id, _conversation, touched = strategy.calls[0]
        assert persona_id == GENERAL_ASSISTANT_ID
        assert len(touched) == 1
        assert touched[0].id is not None  # upsert ran first — the item has its id

    def test_returned_persona_state_persisted_verbatim(self):
        state = {"last_practiced_at": "2026-07-10", "half_life_days": 3.5, "retrievals": 1}
        use_case, conversation_repo, memory_repo = _make_consolidation(
            extraction=self._extraction(),
            assessment_strategies={
                GENERAL_ASSISTANT_ID: FakePersonaAssessmentPort(
                    assessments=[ItemAssessment(item_id=1, memory_type=MemoryType.CONCEPT, persona_state=state)]
                )
            },
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert memory_repo.persona_state_writes == [(MemoryType.CONCEPT, 1, state)]
        assert memory_repo.concepts[0].persona_state == state

    def test_no_dispatch_when_no_strategy_registered(self):
        strategy = FakePersonaAssessmentPort()
        use_case, conversation_repo, memory_repo = _make_consolidation(
            extraction=self._extraction(),
            assessment_strategies={uuid4(): strategy},  # registered for a different persona
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert strategy.calls == []
        assert memory_repo.persona_state_writes == []

    def test_no_dispatch_when_nothing_touched(self):
        strategy = FakePersonaAssessmentPort()
        use_case, conversation_repo, _ = _make_consolidation(
            extraction=ExtractionResult(episodes=[], concepts=[], procedures=[]),
            assessment_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert strategy.calls == []
