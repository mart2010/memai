# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from uuid import UUID

from num2words import num2words

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


ONBOARDING_SCRIPT = """\
When delivering your introduction, cover, in your own natural spoken words (not a verbatim recitation):
- For now, you go by "Vocal Assistant" — a plain placeholder name, not a proper name yet. \
Mention that the user will eventually be able to give you a proper name by voice.
- That you are a personal, fully local voice assistant — no cloud services involved.
- That you remember things about the user across conversations over time.
- That you can take on different specialized personas for particular topics or activities, created \
by talking to you.
- That you are configured entirely by voice, with no apps or settings screens. Today that covers \
choosing your spoken language (done once already) and creating, listing, switching, or removing \
personas. More voice-configurable options will be added over time.
- That the user can ask you to repeat this introduction at any time in the future.
Keep it to roughly 5-8 natural spoken sentences, then invite them to start talking about whatever's \
on their mind."""

_FIRST_LAUNCH_DIRECTIVE = (
    "This is the user's very first conversation with you. Before responding to anything else, "
    "deliver your introduction now, as described below."
)

_SENTENCE_ENDINGS = {".", "!", "?"}


def _is_sentence_end(text: str) -> bool:
    return bool(text) and text[-1] in _SENTENCE_ENDINGS


_BOUNDARY_MARKERS = (
    ("[TOPIC_CONTINUATION]", ConversationBoundaryType.CONTINUATION),
    ("[TOPIC_BREAK]", ConversationBoundaryType.BREAK),
)


def _resolve_boundary_marker(
    buffer: str, is_first_turn: bool
) -> tuple[str, ConversationBoundaryType | None] | None:
    """Resolve an optional boundary marker from the start of `buffer`.

    Returns None while `buffer` is still a proper prefix of a candidate marker (i.e. more
    tokens are needed to disambiguate). [TOPIC_CONTINUATION] is only valid on the first
    turn of a session.
    """
    for marker, boundary_type in _BOUNDARY_MARKERS:
        if buffer.startswith(marker):
            remaining = buffer[len(marker):].lstrip()
            if boundary_type == ConversationBoundaryType.CONTINUATION and not is_first_turn:
                return remaining, None
            return remaining, boundary_type
        if marker.startswith(buffer):
            return None
    return buffer, None


def _try_resolve_prefixes(
    buffer: str, is_first_turn: bool
) -> tuple[str, str | None, ConversationBoundaryType | None] | None:
    """Incrementally resolve an optional [PERSONA:name] prefix followed by an optional
    boundary marker from a streaming LLM response.

    Returns None while more tokens are needed to disambiguate a partial prefix;
    otherwise (remaining_text, persona_name_or_None, boundary_type_or_None).
    """
    if not buffer.startswith("["):
        return buffer, None, None

    if buffer.startswith("[PERSONA:"):
        end = buffer.find("]")
        if end == -1:
            return None
        name = buffer[9:end].strip()
        boundary_result = _resolve_boundary_marker(buffer[end + 1:].lstrip(), is_first_turn)
        if boundary_result is None:
            return None
        remaining, boundary = boundary_result
        return remaining, name, boundary

    if "[PERSONA:".startswith(buffer):
        return None  # still disambiguating "[PERSONA:" itself

    boundary_result = _resolve_boundary_marker(buffer, is_first_turn)
    if boundary_result is None:
        return None
    remaining, boundary = boundary_result
    return remaining, None, boundary


_MARKDOWN_EMPHASIS = re.compile(r"(\*\*\*|\*\*|\*|___|__|_|`)")
_MARKDOWN_HEADER = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MARKDOWN_HRULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$\n?", re.MULTILINE)
_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # pictographs, emoticons, transport, supplemental symbols
    "\U00002600-\U000027BF"  # misc symbols, dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicator symbols (flags)
    "\U0000FE0F"  # variation selector-16
    "\U0000200D"  # zero-width joiner (multi-codepoint emoji sequences)
    "]+"
)


def _strip_markdown(text: str) -> str:
    """Drop markdown headers/rules/emphasis/code markers and emoji — LLMs emit them
    despite instructions not to, and TTS either reads them aloud literally (e.g.
    "hash", "asterisk") or renders them as unpronounceable glyphs."""
    text = _MARKDOWN_HRULE.sub("", text)
    text = _MARKDOWN_HEADER.sub("", text)
    text = _MARKDOWN_EMPHASIS.sub("", text)
    text = _EMOJI.sub("", text)
    return text


# Languages num2words can spell out. Others (ja, ko, zh-cn) are left to Kokoro/espeak's
# own number handling — their number reading is complex enough that a digit-substitution
# approach would likely do more harm than good.
_NUM2WORDS_LANGUAGES = {"en", "fr", "es", "it", "pt"}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _spell_out_numbers(text: str, language_code: str | None) -> str:
    """Convert digit sequences to their spoken form — TTS engines (Kokoro/espeak) read \
literal digits inconsistently, especially outside English."""
    if language_code not in _NUM2WORDS_LANGUAGES:
        return text

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0).replace(",", ".")
        try:
            value: float | int = float(raw) if "." in raw else int(raw)
            return num2words(value, lang=language_code)
        except (ValueError, NotImplementedError):
            return match.group(0)

    return _NUMBER.sub(_replace, text)


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
    if wm.needs_onboarding:
        prompt_parts.insert(0, _FIRST_LAUNCH_DIRECTIVE)
        prompt_parts.append(ONBOARDING_SCRIPT)
    lang = wm.active_persona.response_language
    if lang:
        prompt_parts.append(f"Always respond in the language with IETF code '{lang.code}'. Never switch language unless explicitly asked.")
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
        self._last_turn_end: float | None = None

    async def execute(self, wm: WorkingMemory, audio: bytes, now: datetime) -> TurnResult | None:
        t_start = time.monotonic()
        if self._last_turn_end is not None:
            print(f"[latency] Gap since last turn: {t_start - self._last_turn_end:.1f}s")

        # 1. STT
        text, detected_language = self._stt.transcribe(audio)
        if not text.strip():
            return None
        t_stt_done = time.monotonic()
        print(f"[latency] STT: {t_stt_done - t_start:.2f}s")

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

        # 5. Stream the LLM response; resolve any leading [PERSONA:name]/boundary marker
        # as soon as enough tokens arrive, then synthesise each sentence via TTS as it
        # completes — instead of waiting for the entire reply before speaking a word.
        system_prompt, messages = _compose_working_context(wm, recalled_memories)
        wm.needs_onboarding = False
        response_language = wm.active_persona.response_language
        lang_code = response_language.code if response_language else None
        voice = wm.active_persona.tts_voice

        audio_chunks: list[bytes] = []
        content_parts: list[str] = []
        sentence_buffer = ""
        prefix_buffer = ""
        prefix_resolved = False
        detected_name: str | None = None
        boundary_marker: ConversationBoundaryType | None = None
        t_first_token: float | None = None
        t_first_audio: float | None = None

        async for token in self._llm.complete(messages, system_prompt):
            if t_first_token is None:
                t_first_token = time.monotonic()
                print(f"[latency] LLM first token: {t_first_token - t_stt_done:.2f}s")

            if not prefix_resolved:
                prefix_buffer += token
                resolved = _try_resolve_prefixes(prefix_buffer, is_first_turn)
                if resolved is None:
                    continue
                token, detected_name, boundary_marker = resolved
                prefix_resolved = True

            sentence_buffer += token
            if _is_sentence_end(sentence_buffer.rstrip()):
                processed = _spell_out_numbers(_strip_markdown(sentence_buffer), lang_code)
                content_parts.append(processed)
                t_sentence_ready = time.monotonic()
                audio_chunks.append(self._tts.synthesise(processed, voice))
                if t_first_audio is None:
                    t_first_audio = time.monotonic()
                    print(f"[latency] TTS first chunk: {t_first_audio - t_sentence_ready:.2f}s")
                    print(f"[latency] Total to first audio: {t_first_audio - t_start:.2f}s")
                sentence_buffer = ""

        if not prefix_resolved:
            # Response ended mid-prefix (e.g. a stray "[" with no closing marker) —
            # treat whatever was buffered as plain content rather than dropping it.
            sentence_buffer = prefix_buffer
        if sentence_buffer.strip():
            processed = _spell_out_numbers(_strip_markdown(sentence_buffer), lang_code)
            content_parts.append(processed)
            audio_chunks.append(self._tts.synthesise(processed, voice))
            if t_first_audio is None:
                t_first_audio = time.monotonic()
                print(f"[latency] Total to first audio: {t_first_audio - t_start:.2f}s")

        assistant_content = "".join(content_parts).strip()
        print(f"[latency] Total turn: {time.monotonic() - t_start:.2f}s")

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

        self._last_turn_end = time.monotonic()
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
