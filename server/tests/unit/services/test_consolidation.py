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
    embedding_service: FakeEmbeddingService | None = None,
    worthiness_evaluator: FakeWorthinessEvaluator | None = None,
) -> tuple[ConsolidateMemory, FakeConversationRepository, FakeMemoryRepository]:
    conversation_repo = FakeConversationRepository()
    memory_repo = memory_repo or FakeMemoryRepository()
    use_case = ConsolidateMemory(
        conversation_repo=conversation_repo,
        memory_repo=memory_repo,
        embedding_service=embedding_service or FakeEmbeddingService(),
        extractor=extractor or FakeConsolidationExtractor(result=extraction),
        worthiness_evaluator=worthiness_evaluator or FakeWorthinessEvaluator(worthy=worthy),
        disambiguator=disambiguator or FakeDisambiguationEvaluator(),
        synthesizer=FakeMemorySynthesizer(),
        unit_of_work=FakeUnitOfWork(),
        user_repo=FakeUserRepository(user or User(id=uuid4(), primary_language=Language("en"))),
        assessment_strategies=assessment_strategies,
    )
    return use_case, conversation_repo, memory_repo


_DUMMY_SESSION = UUID("00000000-0000-0000-0000-000000000001")

# Two user turns, well over 40 words combined — clears ConsolidateMemory's cheap
# extraction floor (min_user_turns=2, min_user_words=40, FR-307) by default, so tests
# not specifically about the floor itself don't have to think about it.
_SUBSTANTIAL_USER_TURN_1 = (
    "I've been thinking about learning to play the guitar this year and wanted to "
    "talk through how to actually get started with it."
)
_SUBSTANTIAL_USER_TURN_2 = (
    "I used to play a bit as a teenager but haven't touched an instrument in over "
    "a decade, so I'm basically starting from scratch again."
)


def _seed_ended_conversation(
    conversation_repo: FakeConversationRepository, persona_id: UUID = GENERAL_ASSISTANT_ID,
) -> None:
    conv = Conversation(id=None, started_at=_now(), persona_id=persona_id)
    conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content=_SUBSTANTIAL_USER_TURN_1))
    conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.ASSISTANT, content="That's a great goal, let's talk it through."))
    conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content=_SUBSTANTIAL_USER_TURN_2))
    conv.end(ended_at=_now())
    conv.id = conversation_repo.save_new(conv, session_id=_DUMMY_SESSION)


def _seed_thin_conversation(conversation_repo: FakeConversationRepository) -> None:
    """A single short user turn — below the extraction floor on both dimensions."""
    conv = Conversation(id=None, started_at=_now(), persona_id=GENERAL_ASSISTANT_ID)
    conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content="hello"))
    conv.end(ended_at=_now())
    conv.id = conversation_repo.save_new(conv, session_id=_DUMMY_SESSION)


class TestExtractionFloor:
    """Spec: FR-307 — a conversation too thin to be worth even asking the LLM about
    skips worthiness evaluation AND extraction entirely, purely for cost control."""

    def test_thin_conversation_skips_worthiness_and_extraction(self):
        extractor = FakeConsolidationExtractor()
        worthiness_evaluator = FakeWorthinessEvaluator(worthy=True)
        use_case, conversation_repo, memory_repo = _make_consolidation(
            extractor=extractor, worthiness_evaluator=worthiness_evaluator,
        )
        _seed_thin_conversation(conversation_repo)

        count = use_case.execute()

        assert count == 1
        assert worthiness_evaluator.calls == []
        assert extractor.primary_languages == []
        assert memory_repo.concepts == []
        assert memory_repo.episodes == []

    def test_thin_conversation_marked_not_worthy(self):
        use_case, conversation_repo, _ = _make_consolidation()
        _seed_thin_conversation(conversation_repo)

        use_case.execute()

        [record] = conversation_repo._records.values()
        assert record.consolidated is True
        assert record.worthiness is False

    def test_two_turns_below_word_floor_still_skipped(self):
        """Two user turns, but too few combined words — the word floor is independent
        of the turn-count floor."""
        extractor = FakeConsolidationExtractor()
        use_case, conversation_repo, _ = _make_consolidation(extractor=extractor)
        conv = Conversation(id=None, started_at=_now(), persona_id=GENERAL_ASSISTANT_ID)
        conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content="hi there"))
        conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.ASSISTANT, content="Hello!"))
        conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content="how are you"))
        conv.end(ended_at=_now())
        conv.id = conversation_repo.save_new(conv, session_id=_DUMMY_SESSION)

        use_case.execute()

        assert extractor.primary_languages == []

    def test_conversation_clearing_floor_triggers_extraction(self):
        extractor = FakeConsolidationExtractor()
        use_case, conversation_repo, _ = _make_consolidation(extractor=extractor)
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert len(extractor.primary_languages) == 1


class TestRunConsolidation:
    def test_worthy_conversation_produces_episode(self):
        """Spec: FR-307, TR-703"""
        episode = Episode(id=None, summary="Discussed guitar lessons.", happened_at=_now(), origin_conversation_id=1)
        extraction = ExtractionResult(episodes=[episode], concepts=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=True, extraction=extraction)
        _seed_ended_conversation(conversation_repo)

        count = use_case.execute()

        assert count == 1
        assert len(memory_repo.episodes) == 1

    def test_unworthy_conversation_skips_episodes(self):
        """Spec: FR-307"""
        episode = Episode(id=None, summary="Short chat.", happened_at=_now(), origin_conversation_id=1)
        extraction = ExtractionResult(episodes=[episode], concepts=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=False, extraction=extraction)
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert len(memory_repo.episodes) == 0

    def test_concepts_gated_independently_of_worthiness(self):
        """Spec: FR-307 — concept creation is driven by origin/engagement (see
        TestConceptEngagementGate/TestAuthoredConceptProtection below), not by the
        whole-conversation worthy verdict episodes are gated on."""
        concept = Concept(
            id=None, persona_id=GENERAL_ASSISTANT_ID, name="Recursion",
            description="A function calling itself.", language=Language("en"), origin="organic",
        )
        extraction = ExtractionResult(episodes=[], concepts=[concept])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=False, extraction=extraction)
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert len(memory_repo.concepts) == 1

    def test_conversation_marked_consolidated(self):
        """Spec: TR-703, TR-507"""
        use_case, conversation_repo, _ = _make_consolidation()
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert all(r.consolidated for r in conversation_repo._records.values())

    def test_already_consolidated_conversations_skipped_on_rerun(self):
        """Spec: TR-703"""
        use_case, conversation_repo, _ = _make_consolidation()
        _seed_ended_conversation(conversation_repo)

        use_case.execute()
        count2 = use_case.execute()

        assert count2 == 0

    def test_extractor_receives_user_primary_language(self):
        """Spec: TR-706, INV-10"""
        extractor = FakeConsolidationExtractor()
        use_case, conversation_repo, _ = _make_consolidation(
            extractor=extractor,
            user=User(id=uuid4(), primary_language=Language("fr")),
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert extractor.primary_languages == [Language("fr")]

    def test_extract_episodes_true_when_persona_has_no_strategy(self):
        """Spec: TR-706"""
        extractor = FakeConsolidationExtractor()
        use_case, conversation_repo, _ = _make_consolidation(extractor=extractor)
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert extractor.extract_episodes_calls == [True]

    def test_extract_episodes_false_when_persona_has_registered_strategy(self):
        """Spec: FR-407, TR-706, INV-10 — A persona with a registered assessment strategy (today, only the language
        tutor) owns its own engagement tracking — its lesson conversations are practice,
        not genuine autobiography, so episodes are never even requested."""
        extractor = FakeConsolidationExtractor()
        use_case, conversation_repo, _ = _make_consolidation(
            extractor=extractor,
            assessment_strategies={GENERAL_ASSISTANT_ID: FakePersonaAssessmentPort()},
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert extractor.extract_episodes_calls == [False]


class _CannedSearchMemoryRepository(FakeMemoryRepository):
    """Returns a fixed similarity-search result so merge paths can be exercised."""

    def __init__(self, results) -> None:
        super().__init__()
        self._results = results

    def search(self, embedding, memory_types, top_n, persona_id=None):
        return self._results


class TestCategoryMergeRule:
    def test_existing_category_wins_on_merge(self):
        """Spec: TR-603"""
        existing = Concept(
            id=42, persona_id=GENERAL_ASSISTANT_ID, name="ser vs estar",
            description="Curated pair.", language=Language("es"), category="contrast_pair",
        )
        candidate = Concept(
            id=None, persona_id=GENERAL_ASSISTANT_ID, name="ser vs estar",
            description="New evidence.", language=Language("es"), category="verb",
        )
        memory_repo = _CannedSearchMemoryRepository([(0.95, existing)])  # auto-merge band
        extraction = ExtractionResult(episodes=[], concepts=[candidate])
        use_case, conversation_repo, _ = _make_consolidation(extraction=extraction, memory_repo=memory_repo)
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert candidate.category == "contrast_pair"
        assert candidate.id == 42

    def test_new_category_fills_gap_when_existing_has_none(self):
        """Spec: TR-603"""
        existing = Concept(
            id=42, persona_id=GENERAL_ASSISTANT_ID, name="comer",
            description="To eat.", language=Language("es"), category=None,
        )
        candidate = Concept(
            id=None, persona_id=GENERAL_ASSISTANT_ID, name="comer",
            description="To eat, richer.", language=Language("es"), category="verb",
        )
        memory_repo = _CannedSearchMemoryRepository([(0.95, existing)])
        extraction = ExtractionResult(episodes=[], concepts=[candidate])
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
        return ExtractionResult(episodes=[], concepts=[concept])

    def _memory_repo_with_existing_match(self) -> FakeMemoryRepository:
        existing = Concept(
            id=1, persona_id=GENERAL_ASSISTANT_ID, name="Recursion",
            description="A function calling itself.", language=Language("en"),
        )
        repo = FakeMemoryRepository()
        repo.search_results = [(0.95, existing)]  # auto-merge band
        return repo

    def test_assessment_dispatched_after_upsert_with_ids(self):
        """Spec: TR-704"""
        strategy = FakePersonaAssessmentPort()
        use_case, conversation_repo, _ = _make_consolidation(
            extraction=self._extraction(),
            assessment_strategies={GENERAL_ASSISTANT_ID: strategy},
            memory_repo=self._memory_repo_with_existing_match(),
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert len(strategy.calls) == 1
        persona_id, _conversation, touched = strategy.calls[0]
        assert persona_id == GENERAL_ASSISTANT_ID
        assert len(touched) == 1
        assert touched[0].id is not None  # upsert ran first — the item has its id

    def test_returned_persona_state_persisted_verbatim(self):
        """Spec: TR-704, INV-6"""
        state = {"last_practiced_at": "2026-07-10", "half_life_days": 3.5, "retrievals": 1}
        use_case, conversation_repo, memory_repo = _make_consolidation(
            extraction=self._extraction(),
            assessment_strategies={
                GENERAL_ASSISTANT_ID: FakePersonaAssessmentPort(
                    assessments=[ItemAssessment(item_id=1, memory_type=MemoryType.CONCEPT, persona_state=state)]
                )
            },
            memory_repo=self._memory_repo_with_existing_match(),
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert memory_repo.persona_state_writes == [(MemoryType.CONCEPT, 1, state)]
        assert memory_repo.concepts[0].persona_state == state

    def test_no_dispatch_when_no_strategy_registered(self):
        """Spec: TR-704"""
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
        """Spec: TR-704"""
        strategy = FakePersonaAssessmentPort()
        use_case, conversation_repo, _ = _make_consolidation(
            extraction=ExtractionResult(episodes=[], concepts=[]),
            assessment_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert strategy.calls == []


class TestConceptEngagementGate:
    """Spec: FR-307 — a brand-new organic concept (nothing to merge into) needs real
    user engagement, not just an assistant mention, before it's worth inserting."""

    _GUITAR_VECTOR = [1.0, 0.0]
    _OTHER_VECTOR = [0.0, 1.0]
    _ON_TOPIC_TURN = (
        "I really want to learn guitar this year, maybe starting with some "
        "beginner lessons online to get the basics down."
    )
    _OFF_TOPIC_TURN = (
        "Also I've been really busy with work lately and haven't had much free "
        "time for hobbies at all this month, especially with deadlines piling up."
    )
    _CONCEPT_EMBED_KEY = "Guitar: Playing the guitar."

    def _concept(self) -> Concept:
        return Concept(
            id=None, persona_id=GENERAL_ASSISTANT_ID, name="Guitar",
            description="Playing the guitar.", language=Language("en"), origin="organic",
        )

    def _seed_conversation(self, conversation_repo: FakeConversationRepository, second_turn: str) -> None:
        conv = Conversation(id=None, started_at=_now(), persona_id=GENERAL_ASSISTANT_ID)
        conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content=self._ON_TOPIC_TURN))
        conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.ASSISTANT, content="Sure, let's talk about that."))
        conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content=second_turn))
        conv.end(ended_at=_now())
        conv.id = conversation_repo.save_new(conv, session_id=_DUMMY_SESSION)

    def test_single_qualifying_turn_is_not_enough(self):
        embedding_service = FakeEmbeddingService(
            vector=self._OTHER_VECTOR,
            vectors={self._ON_TOPIC_TURN: self._GUITAR_VECTOR, self._CONCEPT_EMBED_KEY: self._GUITAR_VECTOR},
        )
        extraction = ExtractionResult(episodes=[], concepts=[self._concept()])
        use_case, conversation_repo, memory_repo = _make_consolidation(
            extraction=extraction, embedding_service=embedding_service,
        )
        self._seed_conversation(conversation_repo, self._OFF_TOPIC_TURN)

        use_case.execute()

        assert memory_repo.concepts == []

    def test_two_qualifying_turns_are_enough(self):
        embedding_service = FakeEmbeddingService(
            vector=self._OTHER_VECTOR,
            vectors={
                self._ON_TOPIC_TURN: self._GUITAR_VECTOR,
                self._CONCEPT_EMBED_KEY: self._GUITAR_VECTOR,
            },
        )
        # Second turn also on-topic (reuses the same text/embedding mapping).
        extraction = ExtractionResult(episodes=[], concepts=[self._concept()])
        use_case, conversation_repo, memory_repo = _make_consolidation(
            extraction=extraction, embedding_service=embedding_service,
        )
        conv = Conversation(id=None, started_at=_now(), persona_id=GENERAL_ASSISTANT_ID)
        conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content=self._ON_TOPIC_TURN))
        conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.ASSISTANT, content="Sure, let's talk about that."))
        conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content=self._ON_TOPIC_TURN + " Really excited!"))
        conv.end(ended_at=_now())
        conv.id = conversation_repo.save_new(conv, session_id=_DUMMY_SESSION)
        # The slightly-modified second turn text needs its own mapping too.
        embedding_service._vectors[self._ON_TOPIC_TURN + " Really excited!"] = self._GUITAR_VECTOR

        use_case.execute()

        assert len(memory_repo.concepts) == 1

    def test_merge_into_existing_concept_bypasses_engagement_gate(self):
        """Spec: FR-307 — the gate only applies to a brand-new insert; a real match
        merges regardless of how many turns discuss it."""
        existing = self._concept()
        existing.id = 42
        memory_repo = _CannedSearchMemoryRepository([(0.95, existing)])  # auto-merge band
        extraction = ExtractionResult(episodes=[], concepts=[self._concept()])
        use_case, conversation_repo, memory_repo = _make_consolidation(
            extraction=extraction, memory_repo=memory_repo,
        )
        self._seed_conversation(conversation_repo, self._OFF_TOPIC_TURN)

        use_case.execute()

        assert len(memory_repo.concepts) == 1
        assert memory_repo.concepts[0].id == 42


class TestAuthoredConceptProtection:
    """Spec: FR-407 — curated (authored) content is immutable to live-conversation
    extraction, regardless of which persona the conversation belongs to."""

    def _extraction(self, name: str = "mangiare") -> ExtractionResult:
        concept = Concept(
            id=None, persona_id=GENERAL_ASSISTANT_ID, name=name,
            description="A narrative-flavored extraction blurb about eating pizza.",
            language=Language("it"), origin="organic", engagement_level=EngagementLevel.EXPLORED,
        )
        return ExtractionResult(episodes=[], concepts=[concept])

    def test_close_match_to_authored_concept_is_touched_not_rewritten(self):
        existing = Concept(
            id=1, persona_id=GENERAL_ASSISTANT_ID, name="mangiare",
            description="Curated bundle definition.", language=Language("it"),
            origin="authored", engagement_level=EngagementLevel.MENTIONED,
        )
        memory_repo = FakeMemoryRepository()
        memory_repo.search_results = [(0.8, existing)]  # above authored-protection (0.75), below merge_threshold (0.93)
        use_case, conversation_repo, _ = _make_consolidation(extraction=self._extraction(), memory_repo=memory_repo)
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert memory_repo.concepts[0].description == "Curated bundle definition."
        assert memory_repo.concepts[0].id == 1
        assert memory_repo.concepts[0].engagement_level == EngagementLevel.EXPLORED  # still bumped

    def test_protection_applies_regardless_of_persona_strategy(self):
        """Immutability is driven by Concept.origin, not by which persona is talking —
        even a strategy-less persona (GA) must not rewrite authored content."""
        existing = Concept(
            id=1, persona_id=GENERAL_ASSISTANT_ID, name="mangiare",
            description="Curated bundle definition.", language=Language("it"), origin="authored",
        )
        memory_repo = FakeMemoryRepository()
        memory_repo.search_results = [(0.8, existing)]
        use_case, conversation_repo, _ = _make_consolidation(
            extraction=self._extraction(),
            assessment_strategies={GENERAL_ASSISTANT_ID: FakePersonaAssessmentPort()},
            memory_repo=memory_repo,
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert memory_repo.concepts[0].description == "Curated bundle definition."

    def test_distinct_organic_concept_in_tutor_session_is_free_to_insert(self):
        """Spec: FR-407 — the old blanket "no new items for strategy personas" rule is
        gone: a user going off-curriculum mid-lesson to discuss something real and
        distinct from any curated content is genuine signal, not noise. The default
        FakeEmbeddingService gives every text the same embedding, so the engagement
        gate trivially passes (2 user turns "about" the candidate) — what's under test
        is that the authored-protection check (driven by the canned search similarity,
        0.1, far below the 0.75 threshold) does NOT redirect this into a touch, and
        that a registered strategy no longer blocks the insert outright.
        """
        concept = Concept(
            id=None, persona_id=GENERAL_ASSISTANT_ID, name="Off-curriculum topic",
            description="Something the user brought up, unrelated to any lesson.",
            language=Language("en"), origin="organic",
        )
        existing_authored = Concept(
            id=1, persona_id=GENERAL_ASSISTANT_ID, name="mangiare",
            description="Curated bundle definition.", language=Language("it"), origin="authored",
        )
        memory_repo = FakeMemoryRepository()
        memory_repo.search_results = [(0.1, existing_authored)]  # far below authored-protection threshold
        extraction = ExtractionResult(episodes=[], concepts=[concept])
        use_case, conversation_repo, memory_repo = _make_consolidation(
            extraction=extraction,
            assessment_strategies={GENERAL_ASSISTANT_ID: FakePersonaAssessmentPort()},
            memory_repo=memory_repo,
        )
        _seed_ended_conversation(conversation_repo)

        use_case.execute()

        assert any(c.name == "Off-curriculum topic" for c in memory_repo.concepts)
