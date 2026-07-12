import pytest
from datetime import datetime, UTC, timedelta
from uuid import UUID, uuid4

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


def _tutor_persona() -> AssistantPersona:
    now = _now()
    return AssistantPersona(
        id=uuid4(), name="Tutor", system_prompt="You are a language tutor.",
        languages=[Language("es"), Language("en")], response_language=Language("es"),
        voices={"default": "ef_dora"}, is_system=False, created_at=now, updated_at=now,
        strategy="language_tutor",
    )


def _make_start_session(
    user: User | None = None,
    brief: MemoryBrief | None = None,
    previous: SessionInfo | None = None,
    tail_turns: list[Turn] | None = None,
    threshold_hours: float = 24.0,
) -> tuple[StartSession, FakePersonaRepository]:
    persona_repo = FakePersonaRepository()
    persona_repo.save(_general_assistant())
    use_case = StartSession(
        user_repo=FakeUserRepository(user=user or _user()),
        persona_repo=persona_repo,
        memory_brief_repo=FakeMemoryBriefRepository(brief=brief),
        session_log_reader=FakeSessionLogReader(previous=previous, tail=tail_turns),
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
    selection_strategies: dict | None = None,
    persona_repo: FakePersonaRepository | None = None,
    tts: FakeTTSService | None = None,
) -> tuple[ProcessTurn, FakeMemoryRepository, FakeTurnLogger, FakeLLMService]:
    memory_repo = FakeMemoryRepository()
    wal = FakeTurnLogger()
    llm = FakeLLMService(response=llm_response)
    if persona_repo is None:
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
    process_turn = ProcessTurn(
        stt=FakeSTTService(transcript=stt_transcript, language=detected_language),
        llm=llm,
        tts=tts or FakeTTSService(),
        embedding_service=FakeEmbeddingService(),
        memory_repo=memory_repo,
        recall_detector=FakeRecallIntentDetector(result=recall_result),
        persona_repo=persona_repo,
        turn_logger=wal,
        selection_strategies=selection_strategies,
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

    def test_no_selection_batches_fetched_at_session_start(self):
        # Batches are fetched lazily by ProcessTurn — sessions always start on GA,
        # which has no strategy; a tutor arrives via mid-session switch.
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.selection_batches == {}


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
    async def test_selection_batch_fetched_lazily_on_first_active_turn(self):
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1), context="Anchor: your trip to Madrid.")]
        )
        process_turn, _, _, llm = _make_process_turn(
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert strategy.calls == []  # nothing fetched at session start

        await process_turn.execute(ctx, audio=b"a", now=_now())

        assert strategy.calls == [(GENERAL_ASSISTANT_ID, None, 10)]
        messages, _ = llm.calls[0]
        injected = [m for m in messages if m.role == "system" and "hola" in m.content]
        assert len(injected) == 1
        assert "Anchor: your trip to Madrid." in injected[0].content
        # Injected verbatim as context, never as literal dialogue text
        assert ctx.selection_batches[GENERAL_ASSISTANT_ID] == []  # consumed

    @pytest.mark.asyncio
    async def test_exhausted_batch_is_not_refetched(self):
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1)), SelectedItem(item=_concept("adios", 2))]
        )
        process_turn, _, _, llm = _make_process_turn(
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())
        assert len(ctx.selection_batches[GENERAL_ASSISTANT_ID]) == 1

        await process_turn.execute(ctx, audio=b"b", now=_now())
        assert ctx.selection_batches[GENERAL_ASSISTANT_ID] == []
        second_messages, _ = llm.calls[1]
        assert any("adios" in m.content for m in second_messages if m.role == "system")

        # Batch exhausted — third turn injects nothing and does NOT re-query.
        await process_turn.execute(ctx, audio=b"c", now=_now())
        third_messages, _ = llm.calls[2]
        assert not any("Work this item" in m.content for m in third_messages if m.role == "system")
        assert len(strategy.calls) == 1

    @pytest.mark.asyncio
    async def test_no_fetch_during_onboarding_turn(self):
        strategy = FakePersonaSelectionPort(items=[SelectedItem(item=_concept("hola", 1))])
        process_turn, _, _, _ = _make_process_turn(
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        use_case, _ = _make_start_session(user=User(id=uuid4(), primary_language=None))
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.needs_onboarding

        await process_turn.execute(ctx, audio=b"a", now=_now())
        assert strategy.calls == []  # onboarding turn — no selection

        await process_turn.execute(ctx, audio=b"b", now=_now())
        assert len(strategy.calls) == 1  # onboarding done, lazy fetch resumes

    @pytest.mark.asyncio
    async def test_switched_persona_batch_fetched_on_its_first_turn(self):
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        strategy = FakePersonaSelectionPort(items=[SelectedItem(item=_concept("hola", 1))])
        process_turn, _, _, llm = _make_process_turn(
            llm_response="[PERSONA:Tutor] Hola, empecemos.",
            selection_strategies={tutor.id: strategy},
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())
        assert result is not None and result.persona_switched is not None
        assert strategy.calls == []  # GA was active when this turn's item was selected

        await process_turn.execute(ctx, audio=b"b", now=_now())
        assert strategy.calls == [(tutor.id, None, 10)]
        second_messages, _ = llm.calls[1]
        assert any("hola" in m.content for m in second_messages if m.role == "system")

    @pytest.mark.asyncio
    async def test_focus_marker_replaces_batch(self):
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1)), SelectedItem(item=_concept("adios", 2))],
            focused_items=[SelectedItem(item=_concept("repaso", 3))],
        )
        process_turn, _, _, llm = _make_process_turn(
            llm_response="[FOCUS: review known vocabulary] Sure, let me pull up your review items.",
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None
        assert "[FOCUS" not in result.assistant_content  # marker never spoken
        assert strategy.calls == [
            (GENERAL_ASSISTANT_ID, None, 10),                       # lazy default fetch
            (GENERAL_ASSISTANT_ID, "review known vocabulary", 10),  # focus re-fetch, verbatim
        ]
        assert [s.item.name for s in ctx.selection_batches[GENERAL_ASSISTANT_ID]] == ["repaso"]

        await process_turn.execute(ctx, audio=b"b", now=_now())
        second_messages, _ = llm.calls[1]
        assert any("repaso" in m.content for m in second_messages if m.role == "system")

    @pytest.mark.asyncio
    async def test_combined_persona_and_focus_markers_apply_to_new_persona(self):
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        strategy = FakePersonaSelectionPort(
            focused_items=[SelectedItem(item=_concept("verbos", 1))],
        )
        process_turn, _, _, _ = _make_process_turn(
            llm_response="[PERSONA:Tutor][FOCUS: new verbs] Claro, un verbo nuevo.",
            selection_strategies={tutor.id: strategy},
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        # Focus resolved AFTER the switch — the re-fetch targets the tutor.
        assert strategy.calls == [(tutor.id, "new verbs", 10)]
        assert [s.item.name for s in ctx.selection_batches[tutor.id]] == ["verbos"]

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


class TestSpeakerCast:
    """Inline [SPEAKER:role] tags — per-segment voice switching (two-teacher cast)."""

    def _cast_persona(self, voices: dict[str, str]) -> AssistantPersona:
        now = _now()
        return AssistantPersona(
            id=GENERAL_ASSISTANT_ID, name="Vocal Assistant", system_prompt="Teach.",
            languages=[], response_language=Language("es"), voices=voices,
            is_system=True, created_at=now, updated_at=now,
        )

    async def _run(self, llm_response: str, voices: dict[str, str], session_id=None):
        tts = FakeTTSService()
        process_turn, _, _, _ = _make_process_turn(llm_response=llm_response, tts=tts)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=session_id or uuid4(), started_at=_now())
        ctx.active_persona = self._cast_persona(voices)
        result = await process_turn.execute(ctx, audio=b"a", now=_now())
        return result, tts

    @pytest.mark.asyncio
    async def test_speaker_tags_switch_voice_per_segment(self):
        result, tts = await self._run(
            "[SPEAKER:target_teacher] Hola amigo. [SPEAKER:default] Now in your language.",
            voices={"default": "vd", "target_teacher": "vt"},
        )
        voices_used = [voice for _, voice, _ in tts.synthesised]
        assert voices_used == ["vt", "vd"]
        assert "[SPEAKER" not in result.assistant_content
        assert "Hola amigo." in result.assistant_content

    @pytest.mark.asyncio
    async def test_untagged_response_speaks_with_default_voice(self):
        _, tts = await self._run(
            "Just a plain answer.",
            voices={"default": "vd", "target_teacher": "vt"},
        )
        assert [voice for _, voice, _ in tts.synthesised] == ["vd"]

    @pytest.mark.asyncio
    async def test_unknown_role_falls_back_to_default_voice(self):
        _, tts = await self._run(
            "[SPEAKER:ghost] Hola.",
            voices={"default": "vd", "target_teacher": "vt"},
        )
        assert [voice for _, voice, _ in tts.synthesised] == ["vd"]

    @pytest.mark.asyncio
    async def test_mid_sentence_switch_flushes_open_segment_with_outgoing_voice(self):
        _, tts = await self._run(
            "Listen now [SPEAKER:target_teacher] escucha.",
            voices={"default": "vd", "target_teacher": "vt"},
        )
        assert [(text.strip(), voice) for text, voice, _ in tts.synthesised] == [
            ("Listen now", "vd"),
            ("escucha.", "vt"),
        ]

    @pytest.mark.asyncio
    async def test_rotation_pool_resolved_deterministically_per_session(self):
        voices = {"default": "vd", "target_teacher": "va|vb"}
        _, tts_even = await self._run(
            "[SPEAKER:target_teacher] Hola.", voices, session_id=UUID(int=2),
        )
        _, tts_odd = await self._run(
            "[SPEAKER:target_teacher] Hola.", voices, session_id=UUID(int=3),
        )
        assert [v for _, v, _ in tts_even.synthesised] == ["va"]
        assert [v for _, v, _ in tts_odd.synthesised] == ["vb"]

    @pytest.mark.asyncio
    async def test_non_speaker_brackets_are_plain_content(self):
        result, tts = await self._run(
            "Beware [nota] brackets.",
            voices={"default": "vd"},
        )
        assert "[nota]" in result.assistant_content
        assert [v for _, v, _ in tts.synthesised] == ["vd"]

    @pytest.mark.asyncio
    async def test_dangling_partial_tag_at_stream_end_is_spoken_as_text(self):
        result, _ = await self._run(
            "Hola. [SPEAKER:target",
            voices={"default": "vd", "target_teacher": "vt"},
        )
        assert result.assistant_content.endswith("[SPEAKER:target")


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
