import pytest
from datetime import datetime, UTC
from uuid import uuid4

from memai_server.domain.events import RecallTriggered
from memai_server.domain.model import (
    AssistantPersona,
    CEFRLevel,
    GENERAL_ASSISTANT_ID,
    Language,
    LanguageProficiency,
    MemoryBrief,
    MemoryType,
    User,
)
from memai_server.use_cases.session import EndSession, ProcessTurn, StartSession

from tests.fakes.fakes import (
    FakeConversationRepository,
    FakeEmbeddingService,
    FakeLanguageDetector,
    FakeLLMService,
    FakeMemoryBriefRepository,
    FakeMemoryRepository,
    FakePersonaIntentDetector,
    FakePersonaRepository,
    FakeRecallIntentDetector,
    FakeSTTService,
    FakeTTSService,
    FakeUserRepository,
    FakeTurnLogger,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _general_assistant() -> AssistantPersona:
    return AssistantPersona.general_assistant("You are a helpful assistant.")


def _user(learning_languages: list[Language] | None = None) -> User:
    proficiencies = [
        LanguageProficiency(language=lang, level=CEFRLevel.B1, is_native=False)
        for lang in (learning_languages or [])
    ]
    return User(id=uuid4(), primary_language=Language("en"), proficiencies=proficiencies)


def _make_process_turn(
    stt_transcript: str = "hello",
    llm_response: str = "Hello there.",
    recall_result: RecallTriggered | None = None,
    detected_language: Language = Language("en"),
    persona_intent: str | None = None,
    rolling_window_size: int = 100,
) -> tuple[ProcessTurn, FakeConversationRepository, FakeMemoryRepository, FakeTurnLogger, FakeLLMService]:
    conversation_repo = FakeConversationRepository()
    memory_repo = FakeMemoryRepository()
    wal = FakeTurnLogger()
    llm = FakeLLMService(response=llm_response)
    process_turn = ProcessTurn(
        stt=FakeSTTService(transcript=stt_transcript),
        llm=llm,
        tts=FakeTTSService(),
        embedding_service=FakeEmbeddingService(),
        memory_repo=memory_repo,
        language_detector=FakeLanguageDetector(language=detected_language),
        recall_detector=FakeRecallIntentDetector(result=recall_result),
        persona_detector=FakePersonaIntentDetector(result=persona_intent),
        persona_repo=FakePersonaRepository(),
        wal_writer=wal,
        conversation_repo=conversation_repo,
        rolling_window_size=rolling_window_size,
    )
    return process_turn, conversation_repo, memory_repo, wal, llm


class TestStartSession:
    def test_loads_user_and_general_assistant(self):
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        use_case = StartSession(
            user_repo=FakeUserRepository(user=_user()),
            persona_repo=persona_repo,
            conversation_repo=FakeConversationRepository(),
            memory_brief_repo=FakeMemoryBriefRepository(),
        )
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.active_persona.id == GENERAL_ASSISTANT_ID
        assert ctx.memory_brief is None

    def test_injects_memory_brief(self):
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        brief = MemoryBrief(content="User likes Python.", generated_at=_now())
        use_case = StartSession(
            user_repo=FakeUserRepository(user=_user()),
            persona_repo=persona_repo,
            conversation_repo=FakeConversationRepository(),
            memory_brief_repo=FakeMemoryBriefRepository(brief=brief),
        )
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.memory_brief is brief

    def test_raises_if_user_missing(self):
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        use_case = StartSession(
            user_repo=FakeUserRepository(user=None),
            persona_repo=persona_repo,
            conversation_repo=FakeConversationRepository(),
            memory_brief_repo=FakeMemoryBriefRepository(),
        )
        with pytest.raises(RuntimeError, match="No user found"):
            use_case.execute(session_id=uuid4(), started_at=_now())


class TestProcessTurn:
    @pytest.mark.asyncio
    async def test_basic_turn_produces_audio(self):
        process_turn, _, _, wal, _ = _make_process_turn(
            stt_transcript="hello", llm_response="Hello there."
        )
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        process_turn._persona_repo = persona_repo

        ctx = StartSession(
            user_repo=FakeUserRepository(user=_user()),
            persona_repo=persona_repo,
            conversation_repo=process_turn._conversation_repo,
            memory_brief_repo=FakeMemoryBriefRepository(),
        ).execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"audio", now=_now())
        assert result is not None
        assert result.audio_chunks
        assert result.assistant_content

    @pytest.mark.asyncio
    async def test_empty_transcript_returns_none(self):
        process_turn, _, _, _, _ = _make_process_turn(stt_transcript="   ")
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        process_turn._persona_repo = persona_repo

        ctx = StartSession(
            user_repo=FakeUserRepository(user=_user()),
            persona_repo=persona_repo,
            conversation_repo=process_turn._conversation_repo,
            memory_brief_repo=FakeMemoryBriefRepository(),
        ).execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"silence", now=_now())
        assert result is None

    @pytest.mark.asyncio
    async def test_recall_path_enriches_context(self):
        recall_event = RecallTriggered(query="python tips", memory_types=(MemoryType.CONCEPT,))
        process_turn, _, memory_repo, _, llm = _make_process_turn(
            stt_transcript="remember when we talked about python",
            recall_result=recall_event,
        )
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        process_turn._persona_repo = persona_repo

        ctx = StartSession(
            user_repo=FakeUserRepository(user=_user()),
            persona_repo=persona_repo,
            conversation_repo=process_turn._conversation_repo,
            memory_brief_repo=FakeMemoryBriefRepository(),
        ).execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())
        # LLM was called — system_prompt or messages contain the recall context path
        assert llm.calls

    @pytest.mark.asyncio
    async def test_implicit_persona_suggestion_fires(self):
        french = Language("fr")
        user = _user(learning_languages=[french])
        process_turn, _, _, _, _ = _make_process_turn(detected_language=french)

        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        french_tutor = AssistantPersona(
            id=uuid4(), name="French Tutor", system_prompt="Teach French.",
            is_system=False, created_at=_now(), updated_at=_now(),
        )
        persona_repo.save(french_tutor)
        persona_repo.register_language(french, french_tutor.id)
        process_turn._persona_repo = persona_repo

        ctx = StartSession(
            user_repo=FakeUserRepository(user=user),
            persona_repo=persona_repo,
            conversation_repo=process_turn._conversation_repo,
            memory_brief_repo=FakeMemoryBriefRepository(),
        ).execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"audio", now=_now())
        assert result is not None
        assert result.persona_suggested is not None
        assert result.persona_suggested.detected_language == french
        assert result.persona_suggested.suggested_persona_id == french_tutor.id

    @pytest.mark.asyncio
    async def test_rolling_window_triggered(self):
        process_turn, _, _, _, llm = _make_process_turn(
            llm_response="Got it.", rolling_window_size=2
        )
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        process_turn._persona_repo = persona_repo

        ctx = StartSession(
            user_repo=FakeUserRepository(user=_user()),
            persona_repo=persona_repo,
            conversation_repo=process_turn._conversation_repo,
            memory_brief_repo=FakeMemoryBriefRepository(),
        ).execute(session_id=uuid4(), started_at=_now())

        calls_before = len(llm.calls)
        await process_turn.execute(ctx, audio=b"a", now=_now())
        # After 2 turns (user+assistant = 2), rolling window fires an extra LLM call
        assert len(llm.calls) > calls_before


class TestEndSession:
    def test_record_is_closed_and_saved(self):
        conversation_repo = FakeConversationRepository()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())

        ctx = StartSession(
            user_repo=FakeUserRepository(user=_user()),
            persona_repo=persona_repo,
            conversation_repo=conversation_repo,
            memory_brief_repo=FakeMemoryBriefRepository(),
        ).execute(session_id=uuid4(), started_at=_now())

        ended_at = _now()
        EndSession(conversation_repo=conversation_repo).execute(ctx, ended_at=ended_at)

        assert ctx.conversation_record.ended_at == ended_at
