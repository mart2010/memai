# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from dataclasses import dataclass, field
from datetime import datetime, UTC
from uuid import UUID

from ..domain.events import ConversationBoundaryDetected, ConversationBoundaryType, PersonaSwitched
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
class WorkingMemory:
    session_id: UUID
    started_at: datetime
    user: User
    active_persona: AssistantPersona
    available_personas: list[AssistantPersona]
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


def _strip_conversation_marker(
    text: str, is_first_turn: bool
) -> tuple[str, ConversationBoundaryType | None]:
    """Strip an optional LLM boundary prefix and return the boundary type.

    [TOPIC_CONTINUATION] is only valid on the first turn of a session.
    """
    for prefix, boundary_type in (
        ("[TOPIC_CONTINUATION]", ConversationBoundaryType.CONTINUATION),
        ("[TOPIC_BREAK]", ConversationBoundaryType.BREAK),
    ):
        if text.startswith(prefix):
            stripped = text[len(prefix):].lstrip()
            if boundary_type == ConversationBoundaryType.CONTINUATION and not is_first_turn:
                return stripped, None
            return stripped, boundary_type
    return text, None


def _format_memory_item(item: MemoryItem) -> str:
    from ..domain.model import Concept, Episode, Procedure
    if isinstance(item, Episode):
        return f"[Episode] {item.summary}"
    if isinstance(item, Concept):
        return f"[Concept] {item.name}: {item.description}"
    if isinstance(item, Procedure):
        return f"[Procedure] {item.name}: {item.description}"
    return str(item)


def _compose_working_context(wm: WorkingMemory, recalled_memories: list[MemoryItem]) -> tuple[str, list[Message]]:
    prompt_parts = [wm.active_persona.system_prompt]
    if wm.memory_brief:
        prompt_parts.append(wm.memory_brief.content)
    if recalled_memories:
        lines = "\n".join(f"- {_format_memory_item(m)}" for m in recalled_memories)
        prompt_parts.append(f"Relevant memories:\n{lines}")
    if len(wm.available_personas) > 1:
        persona_lines = "\n".join(f"- {p.name}" for p in wm.available_personas)
        prompt_parts.append(f"Available personas (reply with [PERSONA:name] to switch):\n{persona_lines}")
    system_prompt = "\n\n".join(prompt_parts)

    messages: list[Message] = []

    if wm.session_tail:
        tail_text = "\n".join(f"{t.speaker.value}: {t.content}" for t in wm.session_tail)
        messages.append(Message(role="system", content=f"Tail of previous session:\n{tail_text}"))

    if wm.rolling_summary:
        messages.append(Message(
            role="system",
            content=f"Earlier in this conversation: {wm.rolling_summary}",
        ))
    for turn in wm.recent_turns:
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

    def execute(self, session_id: UUID, started_at: datetime) -> WorkingMemory:
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

        return WorkingMemory(
            session_id=session_id,
            started_at=started_at,
            user=user,
            active_persona=persona,
            available_personas=self._persona_repo.list_all(),
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

    async def execute(self, wm: WorkingMemory, audio: bytes, now: datetime) -> TurnResult | None:
        # 1. STT
        text, detected_language = self._stt.transcribe(audio)
        if not text.strip():
            return None

        # 2. User turn
        user_turn = Turn(timestamp=now, speaker=Speaker.USER, content=text)
        user_turn.language = detected_language

        # 3. Log to file (primary write) + update working memory
        self._turn_logger.append(wm.session_id, user_turn)
        is_first_turn = wm.total_turn_count == 0
        wm.recent_turns.append(user_turn)
        wm.total_turn_count += 1

        # 4. Recall intent → enrich working context from LTM
        recalled_memories: list[MemoryItem] = []
        recall = self._recall_detector.detect(text)
        if recall:
            embedding = self._embedding_service.embed(recall.query)
            recalled_memories = [
                item for _, item in self._memory_repo.search(
                    embedding, recall.memory_types, top_n=5,
                    persona_id=wm.active_persona.id,
                )
            ]

        # 5. Collect LLM response, strip markers, synthesise sentence-by-sentence
        system_prompt, messages = _compose_working_context(wm, recalled_memories)
        raw_response = ""
        async for token in self._llm.complete(messages, system_prompt):
            raw_response += token

        assistant_content, detected_name = _strip_persona_prefix(raw_response.strip())
        assistant_content, boundary_marker = _strip_conversation_marker(assistant_content, is_first_turn)

        voice = wm.active_persona.tts_voice
        audio_chunks: list[bytes] = []
        sentence_buffer = ""
        for ch in assistant_content:
            sentence_buffer += ch
            if _is_sentence_end(sentence_buffer.rstrip()):
                audio_chunks.append(self._tts.synthesise(sentence_buffer, voice))
                sentence_buffer = ""
        if sentence_buffer.strip():
            audio_chunks.append(self._tts.synthesise(sentence_buffer, voice))

        # 6. Conversation boundary event — marker embedded in assistant turn below
        boundary = ConversationBoundaryDetected(boundary_type=boundary_marker) if boundary_marker else None

        # 7. Persona switch from detected prefix
        persona_switched: PersonaSwitched | None = None
        if detected_name:
            match = next(
                (p for p in self._persona_repo.list_all() if p.name.lower() == detected_name.lower()),
                None,
            )
            if match and match.id != wm.active_persona.id:
                persona_switched = PersonaSwitched(
                    from_persona_id=wm.active_persona.id,
                    to_persona_id=match.id,
                )
                wm.active_persona = match

        # 8. Log assistant turn + update working memory
        # Fresh timestamp captures when the LLM finished — gap from `now` reflects response time.
        assistant_turn = Turn(timestamp=datetime.now(UTC), speaker=Speaker.ASSISTANT, content=assistant_content)
        self._turn_logger.append(wm.session_id, assistant_turn, marker=boundary_marker, persona_id=wm.active_persona.id)
        wm.recent_turns.append(assistant_turn)
        wm.total_turn_count += 1

        # 9. Rolling window check
        if (self._rolling_window_size > 0
                and wm.total_turn_count % self._rolling_window_size == 0):
            await self._summarise_window(wm)

        return TurnResult(
            audio_chunks=audio_chunks,
            assistant_content=assistant_content,
            persona_switched=persona_switched,
            conversation_boundary=boundary,
        )

    async def _summarise_window(self, wm: WorkingMemory) -> None:
        n = self._rolling_window_size // 2
        turns = wm.recent_turns[:n]
        excerpt = "\n".join(f"{t.speaker.value}: {t.content}" for t in turns)
        content = excerpt
        if wm.rolling_summary:
            content = f"Previous summary:\n{wm.rolling_summary}\n\nNew turns:\n{excerpt}"
        tokens: list[str] = []
        async for token in self._llm.complete(
            messages=[Message(role="user", content=f"Summarise concisely:\n{content}")],
            system_prompt="You are a conversation summariser. Be brief.",
        ):
            tokens.append(token)
        wm.rolling_summary = "".join(tokens).strip()
        wm.recent_turns = wm.recent_turns[n:]


class EndSession:
    def __init__(self, turn_logger: TurnLogger) -> None:
        self._turn_logger = turn_logger

    def execute(self, wm: WorkingMemory, ended_at: datetime) -> None:
        self._turn_logger.close(wm.session_id, ended_at, clean_exit=True)
