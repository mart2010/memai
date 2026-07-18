import pytest
from datetime import datetime, UTC, timedelta
from uuid import UUID, uuid4

from memai_server.domain.events import ConversationBoundaryType
from memai_server.domain.model import (
    AssistantPersona,
    Concept,
    GENERAL_ASSISTANT_ID,
    Language,
    MemoryBrief,
    Speaker,
    Turn,
    User,
)
from memai_server.services.ports import SelectedItem, SessionInfo
from memai_server.services.session import EndSession, ProcessTurn, StartSession, _compose_working_context

from tests.fakes.fakes import (
    FakeEmbeddingService,
    FakeLanguageDetector,
    FakeLLMService,
    FakeMemoryBriefRepository,
    FakeMemoryRepository,
    FakePersonaRepository,
    FakePersonaSelectionPort,
    FakeRecallGate,
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


def _directive_concept(target_persona_id: UUID, embedding: list[float] | None = None, id_: int = 1) -> Concept:
    """GA-owned Directive concept (FR-207). Default embedding matches
    FakeEmbeddingService's default fixed vector, so it clears the match threshold
    against any turn embedded by a default-constructed FakeEmbeddingService."""
    now = _now()
    return Concept(
        id=id_, persona_id=GENERAL_ASSISTANT_ID, name="Switch to Tutor",
        description="Switch me to the tutor.", language=Language("en"),
        directive={"action": "switch_persona", "target_persona_id": str(target_persona_id)},
        created_at=now, updated_at=now,
        embedding=embedding or [0.1] * 8,
    )


def _make_start_session(
    user: User | None = None,
    brief: MemoryBrief | None = None,
    previous: SessionInfo | None = None,
    tail_turns: list[Turn] | None = None,
    threshold_hours: float = 24.0,
    memory_repo: FakeMemoryRepository | None = None,
) -> tuple[StartSession, FakePersonaRepository]:
    persona_repo = FakePersonaRepository()
    persona_repo.save(_general_assistant())
    use_case = StartSession(
        user_repo=FakeUserRepository(user=user or _user()),
        persona_repo=persona_repo,
        memory_brief_repo=FakeMemoryBriefRepository(brief=brief),
        session_log_reader=FakeSessionLogReader(previous=previous, tail=tail_turns),
        memory_repo=memory_repo or FakeMemoryRepository(),
        session_tail_turns=10,
        session_continuation_threshold_hours=threshold_hours,
    )
    return use_case, persona_repo


def _make_process_turn(
    stt_transcript: str = "hello",
    llm_response: str = "Hello there.",
    detected_language: Language = Language("en"),
    rolling_window_size: int = 100,
    selection_strategies: dict | None = None,
    persona_repo: FakePersonaRepository | None = None,
    tts: FakeTTSService | None = None,
    language_detector: FakeLanguageDetector | None = None,
    default_recall_gate: FakeRecallGate | None = None,
    recall_gates: dict | None = None,
    embedding_service: FakeEmbeddingService | None = None,
    memory_repo: FakeMemoryRepository | None = None,
) -> tuple[ProcessTurn, FakeMemoryRepository, FakeTurnLogger, FakeLLMService]:
    memory_repo = memory_repo or FakeMemoryRepository()
    wal = FakeTurnLogger()
    llm = FakeLLMService(response=llm_response)
    if persona_repo is None:
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
    process_turn = ProcessTurn(
        stt=FakeSTTService(transcript=stt_transcript, language=detected_language),
        llm=llm,
        tts=tts or FakeTTSService(),
        embedding_service=embedding_service or FakeEmbeddingService(),
        memory_repo=memory_repo,
        default_recall_gate=default_recall_gate or FakeRecallGate(),
        persona_repo=persona_repo,
        turn_logger=wal,
        language_detector=language_detector or FakeLanguageDetector(),
        selection_strategies=selection_strategies,
        recall_gates=recall_gates,
        rolling_window_size=rolling_window_size,
    )
    return process_turn, memory_repo, wal, llm


class TestStartSession:
    def test_loads_user_and_general_assistant(self):
        """Spec: TR-301, FR-201"""
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.active_persona.id == GENERAL_ASSISTANT_ID
        assert ctx.memory_brief is None

    def test_injects_memory_brief(self):
        """Spec: FR-109, TR-301"""
        brief = MemoryBrief(content="User likes Python.", created_at=_now(), updated_at=_now())
        use_case, _ = _make_start_session(brief=brief)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.memory_brief is brief

    def test_raises_if_user_missing(self):
        """Spec: TR-301"""
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        use_case = StartSession(
            user_repo=FakeUserRepository(user=None),
            persona_repo=persona_repo,
            memory_brief_repo=FakeMemoryBriefRepository(),
            session_log_reader=FakeSessionLogReader(),
            memory_repo=FakeMemoryRepository(),
        )
        with pytest.raises(RuntimeError, match="No user record found"):
            use_case.execute(session_id=uuid4(), started_at=_now())

    def test_injects_tail_when_previous_session_within_threshold(self):
        """Spec: FR-109, TR-301"""
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=1), clean_exit=True)
        tail = [Turn(timestamp=now, speaker=Speaker.USER, content="earlier turn")]
        use_case, _ = _make_start_session(previous=previous, tail_turns=tail, threshold_hours=24.0)
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        assert len(ctx.session_tail) == 1
        assert ctx.session_tail[0].content == "earlier turn"

    def test_no_tail_when_previous_session_exceeds_threshold(self):
        """Spec: FR-109, TR-301"""
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=30), clean_exit=True)
        tail = [Turn(timestamp=now, speaker=Speaker.USER, content="old turn")]
        use_case, _ = _make_start_session(previous=previous, tail_turns=tail, threshold_hours=24.0)
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        assert ctx.session_tail == []

    def test_no_tail_when_no_previous_session(self):
        """Spec: FR-109, TR-301"""
        use_case, _ = _make_start_session(previous=None)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.session_tail == []

    def test_no_persona_scaffolding_even_with_tail_from_another_persona(self):
        """Spec: FR-207 — a continued session's tail can show a different persona's
        content (e.g. last time's tutor lesson), but with FR-202/FR-203 retired there
        is no persona-listing/switch-instruction block left at all for that tail to
        prime — the system prompt never names another persona, regardless of tail
        content. This is the actual fix for the language-drift this scaffolding used
        to cause; a directive match (not the LLM) is what decides a switch now."""
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=1), clean_exit=True)
        tail = [Turn(timestamp=now, speaker=Speaker.ASSISTANT, content="Ciao! Ripassiamo il vocabolario.")]
        use_case, persona_repo = _make_start_session(previous=previous, tail_turns=tail, threshold_hours=24.0)
        persona_repo.save(_tutor_persona())
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        assert ctx.active_persona.id == GENERAL_ASSISTANT_ID  # FR-201, still true in-memory

        system_prompt, _ = _compose_working_context(ctx, recalled_memories=[])
        assert "Tutor" not in system_prompt
        assert "Available personas" not in system_prompt
        assert "[PERSONA:" not in system_prompt

    def test_no_selection_batches_fetched_at_session_start(self):
        """Spec: TR-306"""
        # Batches are fetched lazily by ProcessTurn — sessions always start on GA,
        # which has no strategy; a tutor arrives via mid-session switch.
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.selection_batches == {}


class TestProcessTurn:
    @pytest.mark.asyncio
    async def test_basic_turn_produces_audio(self):
        """Spec: TR-302, FR-104"""
        process_turn, _, _, _ = _make_process_turn(stt_transcript="hello", llm_response="Hello there.")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"audio", now=_now())
        assert result is not None
        assert result.audio_chunks
        assert result.assistant_content

    @pytest.mark.asyncio
    async def test_empty_transcript_returns_none(self):
        """Spec: FR-103"""
        process_turn, _, _, _ = _make_process_turn(stt_transcript="   ")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"silence", now=_now())
        assert result is None

    @pytest.mark.asyncio
    async def test_recall_search_enriches_context_with_matching_memory(self):
        """Spec: FR-302, FR-309, TR-302, TR-314"""
        process_turn, memory_repo, _, llm = _make_process_turn(
            stt_transcript="remember when we talked about python",
        )
        memory_repo.search_results = [(0.9, _concept("python tips", id_=1))]
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        _, system_prompt = llm.calls[0]
        assert "python tips" in system_prompt

    @pytest.mark.asyncio
    async def test_topic_break_fires_boundary_event(self):
        """Spec: FR-112, TR-304"""
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
        """Spec: FR-112, TR-304"""
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
        """Spec: TR-304"""
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
        """Spec: TR-306, FR-501"""
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
        """Spec: TR-306"""
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
        """Spec: TR-306"""
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
        """Spec: FR-207, TR-306"""
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        memory_repo = FakeMemoryRepository()
        memory_repo.concepts.append(_directive_concept(tutor.id))
        strategy = FakePersonaSelectionPort(items=[SelectedItem(item=_concept("hola", 1))])
        process_turn, _, _, llm = _make_process_turn(
            selection_strategies={tutor.id: strategy},
            persona_repo=persona_repo,
            memory_repo=memory_repo,
        )
        use_case, _ = _make_start_session(memory_repo=memory_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())
        assert result is not None and result.persona_switched is not None
        assert result.persona_switched.to_persona_id == tutor.id
        assert strategy.calls == []  # switch turn — selection/recall skipped this turn (3b)

        await process_turn.execute(ctx, audio=b"b", now=_now())
        assert strategy.calls == [(tutor.id, None, 10)]
        second_messages, _ = llm.calls[1]
        assert any("hola" in m.content for m in second_messages if m.role == "system")

    @pytest.mark.asyncio
    async def test_focus_marker_replaces_batch(self):
        """Spec: FR-502, TR-306"""
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
    async def test_combined_directive_switch_and_focus_marker_apply_to_new_persona(self):
        """Spec: FR-207, FR-502 — a directive switch (decided from the user's own
        utterance, step 3b, before the LLM is called) and a [FOCUS:] marker (resolved
        from the SAME turn's LLM reply) both apply to the new persona: by the time
        FOCUS is resolved, wm.active_persona is already the tutor."""
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        memory_repo = FakeMemoryRepository()
        memory_repo.concepts.append(_directive_concept(tutor.id))
        strategy = FakePersonaSelectionPort(
            focused_items=[SelectedItem(item=_concept("verbos", 1))],
        )
        process_turn, _, _, _ = _make_process_turn(
            llm_response="[FOCUS: new verbs] Claro, un verbo nuevo.",
            selection_strategies={tutor.id: strategy},
            persona_repo=persona_repo,
            memory_repo=memory_repo,
        )
        use_case, _ = _make_start_session(memory_repo=memory_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None and result.persona_switched is not None
        assert result.persona_switched.to_persona_id == tutor.id
        assert strategy.calls == [(tutor.id, "new verbs", 10)]
        assert [s.item.name for s in ctx.selection_batches[tutor.id]] == ["verbos"]

    @pytest.mark.asyncio
    async def test_focus_marker_recognized_after_lead_in_prose(self):
        """Spec: FR-502, TR-306"""
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1))],
            focused_items=[SelectedItem(item=_concept("repaso", 3))],
        )
        process_turn, _, _, _ = _make_process_turn(
            llm_response="Sure, let's review. [FOCUS: old vocabulary] Perfetto.",
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None
        assert "[FOCUS" not in result.assistant_content
        assert "Sure, let's review." in result.assistant_content
        assert "Perfetto." in result.assistant_content
        assert strategy.calls == [
            (GENERAL_ASSISTANT_ID, None, 10),                # lazy default fetch
            (GENERAL_ASSISTANT_ID, "old vocabulary", 10),     # focus re-fetch, verbatim
        ]

    @pytest.mark.asyncio
    async def test_persona_tag_in_llm_response_is_now_inert(self):
        """Spec: FR-207 — the retired [PERSONA:] tag scheme (FR-202) is no longer
        parsed from the LLM's response at all; if a model emits one anyway (it isn't
        instructed to — no persona-listing scaffolding exists in the prompt any more)
        it falls through as literal spoken text, regardless of position."""
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        process_turn, _, _, _ = _make_process_turn(
            llm_response="[PERSONA:Tutor] Hola.",
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None and result.persona_switched is None
        assert "[PERSONA:Tutor]" in result.assistant_content  # falls through as literal text

    @pytest.mark.asyncio
    async def test_no_injection_without_selection_batch(self):
        """Spec: TR-306"""
        process_turn, _, _, llm = _make_process_turn()
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())
        messages, _ = llm.calls[0]
        assert not any("Work this item" in m.content for m in messages)

    @pytest.mark.asyncio
    async def test_rolling_window_triggered(self):
        """Spec: FR-110, TR-309"""
        process_turn, _, _, llm = _make_process_turn(llm_response="Got it.", rolling_window_size=2)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        calls_before = len(llm.calls)
        await process_turn.execute(ctx, audio=b"a", now=_now())
        assert len(llm.calls) > calls_before


class TestRecallGating:
    """Spec: FR-309, TR-314 — RecallGate replaces the old per-turn LLM classification
    call with persona-scoped, local threshold logic. See
    tests/unit/infrastructure/test_recall_gate.py for the real gates' own policy
    logic (word count, dedup threshold); these tests only cover how ProcessTurn
    wires a gate's decisions into whether it searches at all."""

    @pytest.mark.asyncio
    async def test_gate_declining_to_embed_means_no_search_call_at_all(self):
        gate = FakeRecallGate(should_embed_result=False)
        process_turn, memory_repo, _, _ = _make_process_turn(default_recall_gate=gate)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert gate.should_embed_calls == ["hello"]
        assert memory_repo.search_calls == []

    @pytest.mark.asyncio
    async def test_gate_declining_to_search_still_calls_should_embed_first(self):
        gate = FakeRecallGate(should_embed_result=True, should_search_result=False)
        process_turn, memory_repo, _, _ = _make_process_turn(default_recall_gate=gate)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert gate.should_embed_calls == ["hello"]
        assert memory_repo.search_calls == []

    @pytest.mark.asyncio
    async def test_first_turn_ever_passes_none_as_max_similarity(self):
        gate = FakeRecallGate()
        process_turn, _, _, _ = _make_process_turn(default_recall_gate=gate)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert gate.should_search_calls == [None]

    @pytest.mark.asyncio
    async def test_declined_search_reuses_last_real_searchs_cached_results(self):
        """The second turn's should_search() call declines — its LLM context must
        still contain what the first (real) search found, not come up empty."""
        gate = FakeRecallGate(should_search_queue=[True, False])
        process_turn, memory_repo, _, llm = _make_process_turn(default_recall_gate=gate)
        memory_repo.search_results = [(0.9, _concept("python tips", id_=1))]
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"first turn", now=_now())
        await process_turn.execute(ctx, audio=b"second turn", now=_now())

        assert len(memory_repo.search_calls) == 1  # only the first turn actually searched
        _, second_system_prompt = llm.calls[1]
        assert "python tips" in second_system_prompt

    @pytest.mark.asyncio
    async def test_declined_search_matches_against_the_whole_session_history_not_just_the_last_entry(self):
        """Spec: FR-309, TR-314 — nothing new can enter memory mid-session (INV-1), so
        comparing against every prior search this session (not only the most recent
        one) is correct, not just an optimisation. Seeds two historical entries: an
        older one whose embedding exactly matches this turn's, and a more recent,
        unrelated one — proves the older match is found and reused, and the unrelated
        one does not leak into context."""
        embedding_service = FakeEmbeddingService(vector=[1.0, 0.0])
        gate = FakeRecallGate(should_embed_result=True, should_search_result=False)
        process_turn, memory_repo, _, llm = _make_process_turn(
            default_recall_gate=gate, embedding_service=embedding_service,
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        old_match = _concept("old topic", id_=1)
        recent_unrelated = _concept("unrelated topic", id_=2)
        ctx.recall_history[GENERAL_ASSISTANT_ID] = [
            ([1.0, 0.0], [old_match]),  # older, identical embedding to this turn's
            ([0.0, 1.0], [recent_unrelated]),  # more recent, but orthogonal (similarity 0)
        ]

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert gate.should_search_calls[-1] == pytest.approx(1.0)
        _, system_prompt = llm.calls[0]
        assert "old topic" in system_prompt
        assert "unrelated topic" not in system_prompt

    @pytest.mark.asyncio
    async def test_persona_specific_gate_overrides_the_default(self):
        """A persona with its own registered RecallGate uses it instead of
        default_recall_gate — mirrors selection_strategies' per-persona lookup."""
        tutor = _tutor_persona()
        tutor_gate = FakeRecallGate(should_embed_result=False)
        default_gate = FakeRecallGate(should_embed_result=True)
        process_turn, memory_repo, _, _ = _make_process_turn(
            default_recall_gate=default_gate,
            recall_gates={tutor.id: tutor_gate},
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        ctx.active_persona = tutor

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert tutor_gate.should_embed_calls == ["hello"]
        assert default_gate.should_embed_calls == []
        assert memory_repo.search_calls == []


class TestSpeakerCast:
    """Per-segment language detection — voice switching for the two-teacher cast.
    Deliberately whole-segment granularity, never mid-sentence: a segment that's
    mostly the native language but quotes a target-language word stays in the
    native voice (accented, as a real bilingual guide would sound) — see
    _synthesise_segment's comment in session.py."""

    def _cast_persona(self, voices: dict[str, str]) -> AssistantPersona:
        now = _now()
        return AssistantPersona(
            id=GENERAL_ASSISTANT_ID, name="Vocal Assistant", system_prompt="Teach.",
            languages=[], response_language=Language("es"), voices=voices,
            is_system=True, created_at=now, updated_at=now,
        )

    async def _run(
        self, llm_response: str, voices: dict[str, str], detected: list[str | None] | None = None,
        session_id=None,
    ):
        tts = FakeTTSService()
        detector = FakeLanguageDetector(results=detected)
        process_turn, _, _, _ = _make_process_turn(llm_response=llm_response, tts=tts, language_detector=detector)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=session_id or uuid4(), started_at=_now())
        ctx.active_persona = self._cast_persona(voices)
        result = await process_turn.execute(ctx, audio=b"a", now=_now())
        return result, tts, detector

    @pytest.mark.asyncio
    async def test_segment_language_switches_voice(self):
        """Spec: FR-205, TR-305"""
        result, tts, _ = await self._run(
            "Hola amigo. Now in your language.",
            voices={"default": "vd", "es": "vt"},
            detected=["es", "en"],
        )
        assert [voice for _, voice, _ in tts.synthesised] == ["vt", "vd"]
        assert "Hola amigo." in result.assistant_content

    @pytest.mark.asyncio
    async def test_low_confidence_detection_keeps_current_voice(self):
        """Spec: TR-305 — a None result (see FakeLanguageDetector/the real detector's
        min-length gate) must not force a switch."""
        _, tts, _ = await self._run(
            "Just a plain answer.",
            voices={"default": "vd", "es": "vt"},
            detected=[None],
        )
        assert [voice for _, voice, _ in tts.synthesised] == ["vd"]

    @pytest.mark.asyncio
    async def test_detected_language_outside_voices_map_falls_back_to_default(self):
        """Spec: FR-205, TR-305 — e.g. the detector's candidate set includes the
        learner's own language, which is never itself a voices key."""
        _, tts, _ = await self._run(
            "Hola.",
            voices={"default": "vd", "es": "vt"},
            detected=["fr"],
        )
        assert [voice for _, voice, _ in tts.synthesised] == ["vd"]

    @pytest.mark.asyncio
    async def test_no_detection_call_when_persona_has_no_cast_voices(self):
        """Spec: TR-305 — GA and any single-voice persona skip detection entirely,
        not just fall back to default; avoids a wasted call on every ordinary turn."""
        _, tts, detector = await self._run(
            "Just a plain answer.",
            voices={"default": "vd"},
        )
        assert detector.calls == []
        assert [voice for _, voice, _ in tts.synthesised] == ["vd"]

    @pytest.mark.asyncio
    async def test_detection_candidates_are_native_language_plus_cast_voice_keys(self):
        """Spec: TR-305 — restricting candidates to exactly the languages in play
        (not open-domain detection) is what makes short/ambiguous segments tractable."""
        _, _, detector = await self._run(
            "Hola.",
            voices={"default": "vd", "es": "vt"},
            detected=["es"],
        )
        assert detector.calls[0][1] == ("en", "es")  # native (User.primary_language) + cast key

    @pytest.mark.asyncio
    async def test_single_segment_never_splits_mid_sentence(self):
        """Spec: TR-305 — a sentence mixing languages is spoken whole, in whichever
        voice its own overall detected language resolves to; never split mid-sentence
        even when the model writes a bilingual aside in one sentence."""
        _, tts, _ = await self._run(
            "Listen now, escucha bene, all in one breath.",
            voices={"default": "vd", "es": "vt"},
            detected=["en"],
        )
        assert len(tts.synthesised) == 1
        assert tts.synthesised[0][1] == "vd"

    @pytest.mark.asyncio
    async def test_rotation_pool_resolved_deterministically_per_session(self):
        """Spec: FR-206, TR-307"""
        voices = {"default": "vd", "es": "va|vb"}
        _, tts_even, _ = await self._run("Hola.", voices, detected=["es"], session_id=UUID(int=2))
        _, tts_odd, _ = await self._run("Hola.", voices, detected=["es"], session_id=UUID(int=3))
        assert [v for _, v, _ in tts_even.synthesised] == ["va"]
        assert [v for _, v, _ in tts_odd.synthesised] == ["vb"]


class TestResponseLanguageInstruction:
    """Every persona, GA included, is instructed in its own fixed
    `response_language` (FR-105) — detection-independent, and only ever changes on
    explicit request (INV-14). GA mirroring/uninstalled-language reminder
    (formerly FR-113/TR-313) was retired: this replaces its coverage."""

    @pytest.mark.asyncio
    async def test_ga_instruction_ignores_detected_language(self):
        """Spec: FR-105, INV-14 — the GA's system-prompt instruction and TTS voice
        both stay pinned to its own response_language/default voice no matter what
        language the user is detected speaking."""
        for detected in (Language("es"), Language("de")):
            tts = FakeTTSService()
            process_turn, _, _, llm = _make_process_turn(detected_language=detected, tts=tts)
            use_case, _ = _make_start_session()
            ctx = use_case.execute(session_id=uuid4(), started_at=_now())

            await process_turn.execute(ctx, audio=b"hola", now=_now())

            _, system_prompt = llm.calls[0]
            assert "Always respond in the language with IETF code 'en'" in system_prompt
            assert "currently speaking" not in system_prompt
            assert "not installed" not in system_prompt
            assert all(voice == "af_heart" for _, voice, _ in tts.synthesised)

    @pytest.mark.asyncio
    async def test_tutor_persona_keeps_its_own_response_language(self):
        """Spec: FR-105 — a strategy persona (tutor) is instructed in its
        configured response language whatever the user speaks."""
        process_turn, _, _, llm = _make_process_turn(detected_language=Language("en"))
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        ctx.active_persona = _tutor_persona()

        await process_turn.execute(ctx, audio=b"hello", now=_now())

        _, system_prompt = llm.calls[0]
        assert "Always respond in the language with IETF code 'es'" in system_prompt


class TestLanguageTags:
    """User-turn [lang:code] tags — rendered into the LLM context for every persona
    (FR-114): the tutor reads them as production/aside/pronunciation-stumble
    evidence. Rendering-only: stored content and logs stay clean, and a mimicked
    tag in the response is stripped before TTS like every other bracket marker."""

    @pytest.mark.asyncio
    async def test_user_turn_rendered_with_language_tag(self):
        """Spec: FR-114, TR-303"""
        process_turn, _, wal, llm = _make_process_turn(
            stt_transcript="vorrei un caffè", detected_language=Language("it"),
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        messages, _ = llm.calls[0]
        user_messages = [m for m in messages if m.role == "user"]
        assert user_messages[-1].content == "[lang:it] vorrei un caffè"
        # Rendering-only: the stored/logged turn content carries no tag.
        logged_user_turns = [t for _, t in wal.written if t.speaker == Speaker.USER]
        assert logged_user_turns[-1].content == "vorrei un caffè"

    @pytest.mark.asyncio
    async def test_session_tail_user_turns_tagged_assistant_untagged(self):
        """Spec: FR-114, TR-303"""
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=1), clean_exit=True)
        tail = [
            Turn(timestamp=now, speaker=Speaker.USER, content="ciao", language=Language("it")),
            Turn(timestamp=now, speaker=Speaker.ASSISTANT, content="Ciao! Ben fatto."),
        ]
        process_turn, _, _, llm = _make_process_turn()
        use_case, _ = _make_start_session(previous=previous, tail_turns=tail)
        ctx = use_case.execute(session_id=uuid4(), started_at=now)

        await process_turn.execute(ctx, audio=b"a", now=now)

        messages, _ = llm.calls[0]
        tail_message = next(m for m in messages if "Tail of previous session" in m.content)
        assert "user: [lang:it] ciao" in tail_message.content
        assert "assistant: Ciao! Ben fatto." in tail_message.content

    @pytest.mark.asyncio
    async def test_user_turn_without_language_rendered_untagged(self):
        """Spec: FR-114 — no detected language, no tag (e.g. tail lines from logs
        written before the language field existed)."""
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=1), clean_exit=True)
        tail = [Turn(timestamp=now, speaker=Speaker.USER, content="hello there")]
        process_turn, _, _, llm = _make_process_turn()
        use_case, _ = _make_start_session(previous=previous, tail_turns=tail)
        ctx = use_case.execute(session_id=uuid4(), started_at=now)

        await process_turn.execute(ctx, audio=b"a", now=now)

        messages, _ = llm.calls[0]
        tail_message = next(m for m in messages if "Tail of previous session" in m.content)
        assert "user: hello there" in tail_message.content
        assert "[lang:" not in tail_message.content

    @pytest.mark.asyncio
    async def test_mimicked_lang_tag_in_response_is_never_spoken(self):
        """Spec: FR-114, TR-308 — a model imitating the inbound tag convention must
        not have the tag read aloud or logged."""
        tts = FakeTTSService()
        process_turn, _, _, _ = _make_process_turn(
            llm_response="[lang:en] Hello there. [lang:it] Ciao a tutti.", tts=tts,
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert "[lang:" not in result.assistant_content
        assert all("[lang:" not in text for text, _, _ in tts.synthesised)
        assert "Hello there." in result.assistant_content
        assert "Ciao a tutti." in result.assistant_content

    @pytest.mark.asyncio
    async def test_cast_persona_gets_no_generic_response_language_instruction(self):
        """Spec: FR-105, TR-303 — a two-teacher cast deliberately speaks two
        languages per reply; the persona's own prompt owns language use, so the
        generic 'always respond in X' directive is suppressed."""
        now = _now()
        cast_tutor = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach Italian.",
            languages=[Language("it"), Language("en")], response_language=Language("it"),
            voices={"default": "vd", "it": "vt"}, is_system=False,
            created_at=now, updated_at=now, strategy="language_tutor",
        )
        process_turn, _, _, llm = _make_process_turn()
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        ctx.active_persona = cast_tutor

        await process_turn.execute(ctx, audio=b"a", now=now)

        _, system_prompt = llm.calls[0]
        assert "Always respond in the language" not in system_prompt


class TestEndSession:
    def test_turn_logger_closed_with_clean_exit(self):
        """Spec: TR-402"""
        turn_logger = FakeTurnLogger()
        session_id = uuid4()
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=session_id, started_at=_now())

        ended_at = _now()
        EndSession(turn_logger=turn_logger).execute(ctx, ended_at=ended_at)

        assert turn_logger.closed.get(session_id) == ended_at
        assert turn_logger.clean_exits.get(session_id) is True
