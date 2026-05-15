from dataclasses import dataclass, field
from datetime import datetime, UTC
from uuid import UUID

from ..domain.events import BoundaryType, ConversationBoundaryDetected, PersonaSwitched
from ..domain.model import (
    GENERAL_ASSISTANT_ID,
    AssistantPersona,
    MemoryBrief,
    Speaker,
    Turn,
    User,
)
from ..domain.protocols import RecallIntentDetector
from .ports import (
    EmbeddingService,
    LLMService,
    MemoryBriefRepository,
    MemoryItem,
    MemoryRepository,
    Message,
    PersonaRepository,
    SessionLogReader,
    STTService,
    TTSService,
    TurnLogger,
    UserRepository,
)


@dataclass
class SessionContext:
    session_id: UUID
    started_at: datetime
    user: User
    active_persona: AssistantPersona
    memory_brief: MemoryBrief | None
    needs_onboarding: bool = False
    recent_turns: list[Turn] = field(default_factory=list)
    rolling_summary: str | None = None
    total_turn_count: int = 0
    session_tail: list[Turn] = field(default_factory=list)


@dataclass
class TurnResult:
    audio_chunks: list[bytes]
    assistant_content: str
    persona_switched: PersonaSwitched | None = None
    conversation_boundary: ConversationBoundaryDetected | None = None


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


def _strip_conversation_marker(text: str, is_first_turn: bool) -> tuple[str, str | None]:
    """Returns (stripped_text, marker_type_or_None).

    marker_type is 'conversation_boundary' or 'topic_continuation'.
    [TOPIC_CONTINUATION] is only valid on the first turn of a session.
    """
    for marker, marker_type in (
        ("[TOPIC_CONTINUATION]", "topic_continuation"),
        ("[TOPIC_BREAK]", "conversation_boundary"),
    ):
        if text.startswith(marker):
            stripped = text[len(marker):].lstrip()
            if marker_type == "topic_continuation" and not is_first_turn:
                return stripped, None
            return stripped, marker_type
    return text, None


def _format_memory_item(item: MemoryItem) -> str:
    from ..domain.model import Concept, Episode, Procedure
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

    if session.session_tail:
        tail_text = "\n".join(f"{t.speaker.value}: {t.content}" for t in session.session_tail)
        messages.append(Message(role="system", content=f"Tail of previous session:\n{tail_text}"))

    if session.rolling_summary:
        messages.append(Message(
            role="system",
            content=f"Earlier in this conversation: {session.rolling_summary}",
        ))
    for turn in session.recent_turns:
        role = "user" if turn.speaker == Speaker.USER else "assistant"
        messages.append(Message(role=role, content=turn.content))

    return system_prompt, messages


class StartSession:
    def __init__(
        self,
        user_repo: UserRepository,
        persona_repo: PersonaRepository,
        memory_brief_repo: MemoryBriefRepository,
        session_log_reader: SessionLogReader,
        session_tail_turns: int = 10,
        session_continuation_threshold_hours: float = 24.0,
    ) -> None:
        self._user_repo = user_repo
        self._persona_repo = persona_repo
        self._memory_brief_repo = memory_brief_repo
        self._session_log_reader = session_log_reader
        self._tail_turns = session_tail_turns
        self._threshold_hours = session_continuation_threshold_hours

    def execute(self, session_id: UUID, started_at: datetime) -> SessionContext:
        user = self._user_repo.get()
        if user is None:
            raise RuntimeError("No user record found — database not initialised")
        persona = self._persona_repo.get(GENERAL_ASSISTANT_ID)
        if persona is None:
            raise RuntimeError("GeneralAssistant not found — database not initialised")
        needs_onboarding = user.primary_language is None

        session_tail: list[Turn] = []
        if not needs_onboarding:
            previous = self._session_log_reader.get_previous()
            if previous:
                delta_hours = (started_at - previous.ended_at).total_seconds() / 3600
                if delta_hours <= self._threshold_hours:
                    session_tail = self._session_log_reader.read_tail(
                        previous.session_id, self._tail_turns
                    )

        return SessionContext(
            session_id=session_id,
            started_at=started_at,
            user=user,
            active_persona=persona,
            memory_brief=None if needs_onboarding else self._memory_brief_repo.get(),
            needs_onboarding=needs_onboarding,
            session_tail=session_tail,
        )


class ProcessTurn:
    def __init__(
        self,
        stt: STTService,
        llm: LLMService,
        tts: TTSService,
        embedding_service: EmbeddingService,
        memory_repo: MemoryRepository,
        recall_detector: RecallIntentDetector,
        persona_repo: PersonaRepository,
        turn_logger: TurnLogger,
        rolling_window_size: int = 50,
    ) -> None:
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._embedding_service = embedding_service
        self._memory_repo = memory_repo
        self._recall_detector = recall_detector
        self._persona_repo = persona_repo
        self._turn_logger = turn_logger
        self._rolling_window_size = rolling_window_size

    async def execute(self, session: SessionContext, audio: bytes, now: datetime) -> TurnResult | None:
        # 1. STT
        text, detected_language = self._stt.transcribe(audio, session.user.primary_language)
        if not text.strip():
            return None

        # 2. User turn
        user_turn = Turn(timestamp=now, speaker=Speaker.USER, content=text)
        user_turn.language = detected_language

        # 3. Log to file (primary write) + update live context
        self._turn_logger.append(session.session_id, user_turn)
        is_first_turn = session.total_turn_count == 0
        session.recent_turns.append(user_turn)
        session.total_turn_count += 1

        # 4. Recall intent → enrich LLM context
        extra_context: list[MemoryItem] = []
        recall = self._recall_detector.detect(text)
        if recall:
            embedding = self._embedding_service.embed(recall.query)
            extra_context = self._memory_repo.search(embedding, recall.memory_types, top_n=5)

        # 5. Collect LLM response, strip markers, synthesise sentence-by-sentence
        system_prompt, messages = _build_llm_input(session, extra_context)
        raw_response = ""
        async for token in self._llm.complete(messages, system_prompt):
            raw_response += token

        assistant_content, detected_name = _strip_persona_prefix(raw_response.strip())
        assistant_content, boundary_marker = _strip_conversation_marker(assistant_content, is_first_turn)

        audio_chunks: list[bytes] = []
        sentence_buffer = ""
        for ch in assistant_content:
            sentence_buffer += ch
            if _is_sentence_end(sentence_buffer.rstrip()):
                audio_chunks.append(self._tts.synthesise(sentence_buffer))
                sentence_buffer = ""
        if sentence_buffer.strip():
            audio_chunks.append(self._tts.synthesise(sentence_buffer))

        # 6. Conversation boundary event — marker embedded in assistant turn below
        boundary: ConversationBoundaryDetected | None = None
        if boundary_marker:
            btype = BoundaryType.CONTINUATION if boundary_marker == "topic_continuation" else BoundaryType.BREAK
            boundary = ConversationBoundaryDetected(boundary_type=btype)

        # 7. Persona switch from detected prefix
        persona_switched: PersonaSwitched | None = None
        if detected_name:
            match = next(
                (p for p in self._persona_repo.list_all() if p.name.lower() == detected_name.lower()),
                None,
            )
            if match and match.id != session.active_persona.id:
                persona_switched = PersonaSwitched(
                    from_persona_id=session.active_persona.id,
                    to_persona_id=match.id,
                )
                session.active_persona = match

        # 8. Log assistant turn + update live context
        # Fresh timestamp captures when the LLM finished — gap from `now` reflects response time.
        assistant_turn = Turn(timestamp=datetime.now(UTC), speaker=Speaker.ASSISTANT, content=assistant_content)
        self._turn_logger.append(session.session_id, assistant_turn, marker=boundary_marker)
        session.recent_turns.append(assistant_turn)
        session.total_turn_count += 1

        # 9. Rolling window check
        if (self._rolling_window_size > 0
                and session.total_turn_count % self._rolling_window_size == 0):
            await self._summarise_window(session)

        return TurnResult(
            audio_chunks=audio_chunks,
            assistant_content=assistant_content,
            persona_switched=persona_switched,
            conversation_boundary=boundary,
        )

    async def _summarise_window(self, session: SessionContext) -> None:
        n = self._rolling_window_size // 2
        turns = session.recent_turns[:n]
        excerpt = "\n".join(f"{t.speaker.value}: {t.content}" for t in turns)
        tokens: list[str] = []
        async for token in self._llm.complete(
            messages=[Message(role="user", content=f"Summarise concisely:\n{excerpt}")],
            system_prompt="You are a conversation summariser. Be brief.",
        ):
            tokens.append(token)
        session.rolling_summary = "".join(tokens).strip()
        session.recent_turns = session.recent_turns[n:]


class EndSession:
    def __init__(self, turn_logger: TurnLogger) -> None:
        self._turn_logger = turn_logger

    def execute(self, session: SessionContext, ended_at: datetime) -> None:
        self._turn_logger.close(session.session_id, ended_at, clean_exit=True)
