import pytest
from datetime import datetime, UTC, timedelta
from uuid import uuid4

from memai_server.domain.events import ConversationBoundaryType, RecallTriggered
from memai_server.domain.model import (
    AssistantPersona,
    Concept,
    GENERAL_ASSISTANT_ID,
    Language,
    MemoryBrief,
    MemoryType,
    Speaker,
    Turn,
    User,
)
from memai_server.services.ports import SelectedItem, SessionInfo
from memai_server.services.session import EndSession, ProcessTurn, StartSession

from tests.fakes.fakes import (
    FakeEmbeddingService,
    FakeLLMService,
    FakeMemoryBriefRepository,
    FakeMemoryRepository,
    FakePersonaRepository,
    FakePersonaSelectionPort,
    FakeRecallIntentDetector,
    FakeSessionLogReader,
    FakeSTTService,
    FakeTTSService,
    FakeTurnLogger,
    FakeUserRepository,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _general_assistant() -> AssistantPersona:
    return AssistantPersona.general_assistant("You are a helpful assistant.")


def _user() -> User:
    return User(id=uuid4(), primary_language=Language("en"))


def _concept(name: str, id_: int) -> Concept:
    return Concept(
        id=id_, persona_id=GENERAL_ASSISTANT_ID, name=name,
        description=f"{name} description", language=Language("es"),
    )


def _make_start_session(
    user: User | None = None,
    brief: MemoryBrief | None = None,
    previous: SessionInfo | None = None,
    tail_turns: list[Turn] | None = None,
    threshold_hours: float = 24.0,
    selection_strategies: dict | None = None,
) -> tuple[StartSession, FakePersonaRepository]:
    persona_repo = FakePersonaRepository()
    persona_repo.save(_general_assistant())
    use_case = StartSession(
        user_repo=FakeUserRepository(user=user or _user()),
        persona_repo=persona_repo,
        memory_brief_repo=FakeMemoryBriefRepository(brief=brief),
        session_log_reader=FakeSessionLogReader(previous=previous, tail=tail_turns),
        selection_strategies=selection_strategies,
        session_tail_turns=10,
        session_continuation_threshold_hours=threshold_hours,
    )
    return use_case, persona_repo


def _make_process_turn(
    stt_transcript: str = "hello",
    llm_response: str = "Hello there.",
    recall_result: RecallTriggered | None = None,
    detected_language: Language = Language("en"),
    rolling_window_size: int = 100,
) -> tuple[ProcessTurn, FakeMemoryRepository, FakeTurnLogger, FakeLLMService]:
    memory_repo = FakeMemoryRepository()
    wal = FakeTurnLogger()
    llm = FakeLLMService(response=llm_response)
    persona_repo = FakePersonaRepository()
    persona_repo.save(_general_assistant())
    process_turn = ProcessTurn(
        stt=FakeSTTService(transcript=stt_transcript, language=detected_language),
        llm=llm,
        tts=FakeTTSService(),
        embedding_service=FakeEmbeddingService(),
        memory_repo=memory_repo,
        recall_detector=FakeRecallIntentDetector(result=recall_result),
        persona_repo=persona_repo,
        turn_logger=wal,
        rolling_window_size=rolling_window_size,
    )
    return process_turn, memory_repo, wal, llm


class TestStartSession:
    def test_loads_user_and_general_assistant(self):
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.active_persona.id == GENERAL_ASSISTANT_ID
        assert ctx.memory_brief is None

    def test_injects_memory_brief(self):
        brief = MemoryBrief(content="User likes Python.", created_at=_now(), updated_at=_now())
        use_case, _ = _make_start_session(brief=brief)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.memory_brief is brief

    def test_raises_if_user_missing(self):
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        use_case = StartSession(
            user_repo=FakeUserRepository(user=None),
            persona_repo=persona_repo,
            memory_brief_repo=FakeMemoryBriefRepository(),
            session_log_reader=FakeSessionLogReader(),
        )
        with pytest.raises(RuntimeError, match="No user record found"):
            use_case.execute(session_id=uuid4(), started_at=_now())

    def test_injects_tail_when_previous_session_within_threshold(self):
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=1), clean_exit=True)
        tail = [Turn(timestamp=now, speaker=Speaker.USER, content="earlier turn")]
        use_case, _ = _make_start_session(previous=previous, tail_turns=tail, threshold_hours=24.0)
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        assert len(ctx.session_tail) == 1
        assert ctx.session_tail[0].content == "earlier turn"

    def test_no_tail_when_previous_session_exceeds_threshold(self):
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=30), clean_exit=True)
        tail = [Turn(timestamp=now, speaker=Speaker.USER, content="old turn")]
        use_case, _ = _make_start_session(previous=previous, tail_turns=tail, threshold_hours=24.0)
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        assert ctx.session_tail == []

    def test_no_tail_when_no_previous_session(self):
        use_case, _ = _make_start_session(previous=None)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.session_tail == []

    def test_selection_batch_empty_when_no_strategy_registered(self):
        # GA registers no selection strategy — the no-op path.
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.selection_batch == []

    def test_selection_batch_fetched_at_session_start(self):
        item = SelectedItem(item=_concept("hola", 1), context="Anchor: your trip to Madrid.")
        strategy = FakePersonaSelectionPort(items=[item])
        use_case, _ = _make_start_session(
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.selection_batch == [item]
        assert len(strategy.calls) == 1
        assert strategy.calls[0][0] == GENERAL_ASSISTANT_ID

    def test_selection_skipped_during_onboarding(self):
        strategy = FakePersonaSelectionPort(items=[SelectedItem(item=_concept("hola", 1))])
        use_case, _ = _make_start_session(
            user=User(id=uuid4(), primary_language=None),  # onboarding not done
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.selection_batch == []
        assert strategy.calls == []


class TestProcessTurn:
    @pytest.mark.asyncio
    async def test_basic_turn_produces_audio(self):
        process_turn, _, _, _ = _make_process_turn(stt_transcript="hello", llm_response="Hello there.")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"audio", now=_now())
        assert result is not None
        assert result.audio_chunks
        assert result.assistant_content

    @pytest.mark.asyncio
    async def test_empty_transcript_returns_none(self):
        process_turn, _, _, _ = _make_process_turn(stt_transcript="   ")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"silence", now=_now())
        assert result is None

    @pytest.mark.asyncio
    async def test_recall_path_enriches_context(self):
        recall_event = RecallTriggered(query="python tips", memory_types=(MemoryType.CONCEPT,))
        process_turn, _, _, llm = _make_process_turn(
            stt_transcript="remember when we talked about python",
            recall_result=recall_event,
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())
        assert llm.calls

    @pytest.mark.asyncio
    async def test_topic_break_fires_boundary_event(self):
        process_turn, _, wal, _ = _make_process_turn(llm_response="[TOPIC_BREAK] Sure, new topic.")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        await process_turn.execute(ctx, audio=b"a", now=_now())  # advance past first turn
        wal.markers.clear()

        result = await process_turn.execute(ctx, audio=b"b", now=_now())
        assert result is not None
        assert result.conversation_boundary is not None
        assert result.conversation_boundary.boundary_type == ConversationBoundaryType.BREAK
        assert any(m == ConversationBoundaryType.BREAK for _, m in wal.markers)

    @pytest.mark.asyncio
    async def test_topic_continuation_fires_on_first_turn(self):
        process_turn, _, wal, _ = _make_process_turn(llm_response="[TOPIC_CONTINUATION] Yes, continuing.")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())
        assert result is not None
        assert result.conversation_boundary is not None
        assert result.conversation_boundary.boundary_type == ConversationBoundaryType.CONTINUATION
        assert any(m == ConversationBoundaryType.CONTINUATION for _, m in wal.markers)

    @pytest.mark.asyncio
    async def test_topic_continuation_ignored_on_non_first_turn(self):
        process_turn, _, wal, _ = _make_process_turn(llm_response="[TOPIC_CONTINUATION] Continuing.")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"first", now=_now())
        wal.markers.clear()

        result = await process_turn.execute(ctx, audio=b"second", now=_now())
        assert result is not None
        assert result.conversation_boundary is None
        assert not wal.markers

    @pytest.mark.asyncio
    async def test_selected_item_injected_into_llm_context(self):
        process_turn, _, _, llm = _make_process_turn()
        use_case, _ = _make_start_session(
            selection_strategies={
                GENERAL_ASSISTANT_ID: FakePersonaSelectionPort(
                    items=[SelectedItem(item=_concept("hola", 1), context="Anchor: your trip to Madrid.")]
                )
            },
        )
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        messages, _ = llm.calls[0]
        injected = [m for m in messages if m.role == "system" and "hola" in m.content]
        assert len(injected) == 1
        assert "Anchor: your trip to Madrid." in injected[0].content
        # Injected verbatim as context, never as literal dialogue text
        assert ctx.selection_batch == []  # consumed

    @pytest.mark.asyncio
    async def test_selection_batch_consumed_one_item_per_turn(self):
        items = [SelectedItem(item=_concept("hola", 1)), SelectedItem(item=_concept("adios", 2))]
        process_turn, _, _, llm = _make_process_turn()
        use_case, _ = _make_start_session(
            selection_strategies={GENERAL_ASSISTANT_ID: FakePersonaSelectionPort(items=items)},
        )
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())
        assert len(ctx.selection_batch) == 1

        await process_turn.execute(ctx, audio=b"b", now=_now())
        assert ctx.selection_batch == []
        second_messages, _ = llm.calls[1]
        assert any("adios" in m.content for m in second_messages if m.role == "system")

        # Batch exhausted — third turn injects nothing.
        await process_turn.execute(ctx, audio=b"c", now=_now())
        third_messages, _ = llm.calls[2]
        assert not any("Work this item" in m.content for m in third_messages if m.role == "system")

    @pytest.mark.asyncio
    async def test_no_injection_without_selection_batch(self):
        process_turn, _, _, llm = _make_process_turn()
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())
        messages, _ = llm.calls[0]
        assert not any("Work this item" in m.content for m in messages)

    @pytest.mark.asyncio
    async def test_rolling_window_triggered(self):
        process_turn, _, _, llm = _make_process_turn(llm_response="Got it.", rolling_window_size=2)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        calls_before = len(llm.calls)
        await process_turn.execute(ctx, audio=b"a", now=_now())
        assert len(llm.calls) > calls_before


class TestEndSession:
    def test_turn_logger_closed_with_clean_exit(self):
        turn_logger = FakeTurnLogger()
        session_id = uuid4()
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=session_id, started_at=_now())

        ended_at = _now()
        EndSession(turn_logger=turn_logger).execute(ctx, ended_at=ended_at)

        assert turn_logger.closed.get(session_id) == ended_at
        assert turn_logger.clean_exits.get(session_id) is True
