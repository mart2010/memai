from copy import copy
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from ..domain.events import PersonaSuggested, PersonaSwitched
from ..domain.model import (
    AssistantPersona,
    ConversationRecord,
    GENERAL_ASSISTANT_ID,
    Language,
    LiveConversation,
    MemoryBrief,
    Speaker,
    Turn,
    User,
    should_suggest_persona,
)
from ..domain.protocols import LanguageDetector, PersonaIntentDetector, RecallIntentDetector
from .ports import (
    ConversationRepository,
    EmbeddingService,
    LLMService,
    MemoryBriefRepository,
    MemoryItem,
    MemoryRepository,
    Message,
    PersonaRepository,
    STTService,
    TTSService,
    UserRepository,
    TurnLogger,
)


@dataclass
class SessionContext:
    session_id: UUID
    user: User
    active_persona: AssistantPersona
    live_conversation: LiveConversation
    conversation_record: ConversationRecord
    memory_brief: MemoryBrief | None


@dataclass
class TurnResult:
    audio_chunks: list[bytes]
    assistant_content: str
    persona_switched: PersonaSwitched | None = None
    persona_suggested: PersonaSuggested | None = None


_SENTENCE_ENDINGS = {".", "!", "?"}


def _is_sentence_end(text: str) -> bool:
    return bool(text) and text[-1] in _SENTENCE_ENDINGS


def _strip_persona_prefix(text: str) -> tuple[str, str | None]:
    """Returns (stripped_text, persona_name_or_None)."""
    if text.startswith("[PERSONA:"):
        end = text.find("]")
        if end != -1:
            name = text[9:end].strip()
            return text[end + 1:].lstrip(), name
    return text, None


def _format_memory_item(item: MemoryItem) -> str:
    from ..domain.model import Episode, Concept, Procedure
    if isinstance(item, Episode):
        return f"[Memory] {item.summary}"
    if isinstance(item, Concept):
        return f"[Concept] {item.name}: {item.description}"
    if isinstance(item, Procedure):
        return f"[Procedure] {item.name}: {'; '.join(item.steps)}"
    return str(item)


def _build_llm_input(session: SessionContext, extra_context: list[MemoryItem]) -> tuple[str, list[Message]]:
    prompt_parts = [session.active_persona.system_prompt]
    if session.memory_brief:
        prompt_parts.append(session.memory_brief.content)
    if extra_context:
        lines = "\n".join(f"- {_format_memory_item(m)}" for m in extra_context)
        prompt_parts.append(f"Relevant memories:\n{lines}")
    system_prompt = "\n\n".join(prompt_parts)

    messages: list[Message] = []
    if session.live_conversation.rolling_summary:
        messages.append(Message(
            role="system",
            content=f"Earlier in this conversation: {session.live_conversation.rolling_summary}",
        ))
    for turn in session.live_conversation.recent_turns:
        role = "user" if turn.speaker == Speaker.USER else "assistant"
        messages.append(Message(role=role, content=turn.content))

    return system_prompt, messages


class StartSession:
    def __init__(
        self,
        user_repo: UserRepository,
        persona_repo: PersonaRepository,
        conversation_repo: ConversationRepository,
        memory_brief_repo: MemoryBriefRepository,
    ) -> None:
        self._user_repo = user_repo
        self._persona_repo = persona_repo
        self._conversation_repo = conversation_repo
        self._memory_brief_repo = memory_brief_repo

    def execute(self, session_id: UUID, started_at: datetime) -> SessionContext:
        user = self._user_repo.get()
        if user is None:
            raise RuntimeError("No user found — database not initialised")
        persona = self._persona_repo.get(GENERAL_ASSISTANT_ID)
        if persona is None:
            raise RuntimeError("GeneralAssistant not found — database not initialised")
        brief = self._memory_brief_repo.get()
        live = LiveConversation(started_at=started_at, persona_id=persona.id)
        record = ConversationRecord(id=session_id, started_at=started_at, persona_snapshot=copy(persona))
        self._conversation_repo.save(record)
        return SessionContext(
            session_id=session_id,
            user=user,
            active_persona=persona,
            live_conversation=live,
            conversation_record=record,
            memory_brief=brief,
        )


class ProcessTurn:
    def __init__(
        self,
        stt: STTService,
        llm: LLMService,
        tts: TTSService,
        embedding_service: EmbeddingService,
        memory_repo: MemoryRepository,
        language_detector: LanguageDetector,
        recall_detector: RecallIntentDetector,
        persona_detector: PersonaIntentDetector,
        persona_repo: PersonaRepository,
        wal_writer: TurnLogger,
        conversation_repo: ConversationRepository,
        rolling_window_size: int = 50,
    ) -> None:
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._embedding_service = embedding_service
        self._memory_repo = memory_repo
        self._language_detector = language_detector
        self._recall_detector = recall_detector
        self._persona_detector = persona_detector
        self._persona_repo = persona_repo
        self._wal_writer = wal_writer
        self._conversation_repo = conversation_repo
        self._rolling_window_size = rolling_window_size

    async def execute(self, session: SessionContext, audio: bytes, now: datetime) -> TurnResult | None:
        # 1. STT
        text = self._stt.transcribe(audio, session.user.primary_language)
        if not text.strip():
            return None

        # 2. User turn + language detection
        user_turn = Turn(timestamp=now, speaker=Speaker.USER, content=text)
        user_turn.language = self._language_detector.detect(text)

        # 3. WAL write (primary), then add to aggregates + persist
        self._wal_writer.append(session.session_id, user_turn)
        session.live_conversation.add_turn(user_turn)
        session.conversation_record.add_turn(user_turn)
        self._conversation_repo.save(session.conversation_record)

        # 4. Recall intent → enrich context
        extra_context: list[MemoryItem] = []
        recall = self._recall_detector.detect(text)
        if recall:
            embedding = self._embedding_service.embed(recall.query)
            extra_context = self._memory_repo.search(embedding, recall.memory_types, top_n=5)

        # 5. Build LLM input + stream response sentence-by-sentence into TTS
        system_prompt, messages = _build_llm_input(session, extra_context)
        raw_response = ""
        audio_chunks: list[bytes] = []
        sentence_buffer = ""

        async for token in self._llm.complete(messages, system_prompt):
            raw_response += token
            sentence_buffer += token
            if _is_sentence_end(sentence_buffer.rstrip()):
                audio_chunks.append(self._tts.synthesise(sentence_buffer))
                sentence_buffer = ""

        if sentence_buffer.strip():
            audio_chunks.append(self._tts.synthesise(sentence_buffer))

        # 6. Detect persona intent in LLM response + strip prefix
        assistant_content, detected_name = _strip_persona_prefix(raw_response.strip())
        persona_switched: PersonaSwitched | None = None
        if detected_name:
            match = next(
                (p for p in self._persona_repo.list_all() if p.name.lower() == detected_name.lower()),
                None,
            )
            if match and match.id != session.live_conversation.persona_id:
                persona_switched = PersonaSwitched(
                    from_persona_id=session.live_conversation.persona_id,
                    to_persona_id=match.id,
                )
                session.live_conversation.persona_id = match.id
                session.active_persona = match

        # 7. Assistant turn — WAL write + add to aggregates + persist
        assistant_turn = Turn(timestamp=now, speaker=Speaker.ASSISTANT, content=assistant_content)
        self._wal_writer.append(session.session_id, assistant_turn)
        session.live_conversation.add_turn(assistant_turn)
        session.conversation_record.add_turn(assistant_turn)
        self._conversation_repo.save(session.conversation_record)

        # 8. Implicit persona suggestion (runs on user turn)
        persona_suggested: PersonaSuggested | None = None
        if should_suggest_persona(user_turn, session.user, session.live_conversation.persona_id):
            candidate = self._persona_repo.find_by_language(user_turn.language)
            if candidate:
                persona_suggested = PersonaSuggested(
                    detected_language=user_turn.language,
                    suggested_persona_id=candidate.id,
                )

        # 9. Rolling window check
        if (self._rolling_window_size > 0
                and session.live_conversation.total_turn_count % self._rolling_window_size == 0):
            await self._summarise_window(session)

        return TurnResult(
            audio_chunks=audio_chunks,
            assistant_content=assistant_content,
            persona_switched=persona_switched,
            persona_suggested=persona_suggested,
        )

    async def _summarise_window(self, session: SessionContext) -> None:
        n = self._rolling_window_size // 2
        turns = session.live_conversation.recent_turns[:n]
        excerpt = "\n".join(f"{t.speaker.value}: {t.content}" for t in turns)
        tokens: list[str] = []
        async for token in self._llm.complete(
            messages=[Message(role="user", content=f"Summarise concisely:\n{excerpt}")],
            system_prompt="You are a conversation summariser. Be brief.",
        ):
            tokens.append(token)
        session.live_conversation.apply_rolling_summary("".join(tokens).strip(), n)


class EndSession:
    def __init__(self, conversation_repo: ConversationRepository) -> None:
        self._conversation_repo = conversation_repo

    def execute(self, session: SessionContext, ended_at: datetime) -> None:
        session.conversation_record.end(ended_at)
        self._conversation_repo.save(session.conversation_record)
