# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from uuid import UUID

from num2words import num2words

from ..domain.events import ConversationBoundaryDetected, ConversationBoundaryType, PersonaSwitched
from ..domain.model import (
    DEFAULT_VOICE_ROLE,
    GENERAL_ASSISTANT_ID,
    SUPPORTED_LANGUAGES,
    AssistantPersona,
    Language,
    MemoryBrief,
    Speaker,
    Turn,
    User,
)
from ..domain.protocols import RecallIntentDetector
from .ports import (
    EmbeddingService,
    LanguageDetector,
    LLMService,
    MemoryBriefRepository,
    MemoryItem,
    MemoryRepository,
    Message,
    PersonaRepository,
    PersonaSelectionPort,
    SelectedItem,
    SessionLogReader,
    STTService,
    TTSService,
    TurnLogger,
    UserRepository,
)

# Opt-in, off by default: persona-switch/selection/cast-voice tracing for evaluating
# a candidate LLM's tag-emission reliability (see server/tests/e2e/, and docs/PLAN.md
# Phase 12's live smoke + gemma3:27b follow-up, which is what this was built for).
# Gated because some of these lines echo conversation content to stdout — unlike the
# existing unconditional [latency]/[offline]/[strategy] lines, which never do.
_TUTOR_DEBUG = os.environ.get("MEMAI_TEST_TUTOR_DEBUG") is not None


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
    # Persona-selected items, keyed by persona id and consumed one per turn. Fetched
    # lazily on the first turn a strategy-bearing persona is active (sessions start on
    # GA; tutors arrive via mid-session switch) — key presence means "already fetched",
    # so an exhausted batch is not re-queried. A [FOCUS: ...] marker replaces the active
    # persona's batch with a focus-steered re-fetch.
    selection_batches: dict[UUID, list[SelectedItem]] = field(default_factory=dict)


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


def _split_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """Split `buffer` into (complete sentences, trailing incomplete remainder). A
    single call's `buffer` isn't guaranteed to hold at most one sentence — the
    force-resolve fallback (see `_try_resolve_prefixes(..., force=True)`) can hand
    back an entire multi-sentence response in one piece when the LLM's response
    never grew a tag to scan for, so this must find every boundary, not just check
    whether the buffer as a whole ends on one."""
    sentences: list[str] = []
    start = 0
    for i, ch in enumerate(buffer):
        if ch in _SENTENCE_ENDINGS:
            sentences.append(buffer[start:i + 1])
            start = i + 1
    return sentences, buffer[start:]


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


# How much lead-in prose to buffer while scanning for a [PERSONA:]/[FOCUS:] tag
# appearing anywhere in a streaming response (not just position zero) before giving up
# on finding one. Widened from a position-zero-only check after live testing (see
# docs/PLAN.md Phase 12 live smoke + gemma3:27b follow-up) showed real models — weak
# and strong alike — routinely preface a tag-bearing reply with conversational lead-in
# (an apology, an acknowledgment), especially when recovering from apparent confusion —
# exactly when a reliable switch matters most, and exactly what position-zero matching
# can never catch. This is a latency/reliability tradeoff, not a free win: a turn with
# no tag at all now waits up to this many characters (or stream end, whichever comes
# first) before speech can start, instead of usually resolving within the first token.
# Value is a placeholder pending real tuning against live turns, same posture as the
# 0.93/0.75 upsert thresholds.
_PREFIX_SCAN_WINDOW_CHARS = 200


def _extract_tag(buffer: str, tag: str) -> tuple[str, str] | None:
    """If `tag` occurs anywhere in `buffer` and is already closed by a ']', remove it —
    splicing the surrounding text back together — and return (buffer_without_tag,
    payload). Returns None if `tag` isn't present yet, or is open but not yet closed.
    """
    start = buffer.find(tag)
    if start == -1:
        return None
    end = buffer.find("]", start)
    if end == -1:
        return None  # tag opened but not closed yet — wait for more tokens
    payload = buffer[start + len(tag):end].strip()
    return buffer[:start] + buffer[end + 1:], payload


def _tag_might_still_open(buffer: str, tag: str) -> bool:
    """True if some suffix of `buffer` is a proper, non-empty prefix of `tag` — i.e.
    the buffer's tail might currently be mid-way through typing this tag's opening
    bracket sequence, so it's too early to conclude the tag isn't coming."""
    return any(buffer.endswith(tag[:i]) for i in range(1, len(tag)))


def _try_resolve_prefixes(
    buffer: str, is_first_turn: bool, force: bool = False
) -> tuple[str, str | None, str | None, ConversationBoundaryType | None] | None:
    """Resolve the optional response-prefix markers [PERSONA:name] and [FOCUS: wish]
    from anywhere within the first `_PREFIX_SCAN_WINDOW_CHARS` characters of a
    streaming LLM response — not just position zero, see that constant's docstring —
    followed by an optional boundary marker at the very start of whatever remains.

    Returns None while more tokens might change the outcome (still inside the scan
    window with a tag not yet found, or the tail of `buffer` might be an in-progress
    tag) — unless `force` is set, which finalizes against exactly what's buffered now
    (used once the LLM stream itself has ended and no further tokens are coming, so
    nothing left unresolved can ever resolve). Otherwise returns (remaining_text,
    persona_name_or_None, focus_or_None, boundary_type_or_None).
    """
    remaining = buffer
    persona_name: str | None = None
    focus: str | None = None

    extracted = _extract_tag(remaining, "[PERSONA:")
    if extracted is not None:
        remaining, persona_name = extracted

    extracted = _extract_tag(remaining, "[FOCUS:")
    if extracted is not None:
        remaining, focus = extracted

    if not force:
        still_waiting = (
            (persona_name is None and _tag_might_still_open(remaining, "[PERSONA:"))
            or (focus is None and _tag_might_still_open(remaining, "[FOCUS:"))
            or (
                (persona_name is None or focus is None)
                and len(remaining) < _PREFIX_SCAN_WINDOW_CHARS
            )
        )
        if still_waiting:
            return None

    text = remaining.lstrip()
    boundary_result = _resolve_boundary_marker(text, is_first_turn)
    if boundary_result is None:
        if not force:
            return None
        boundary_result = (text, None)  # dangling partial boundary marker at stream end — plain text
    text, boundary = boundary_result
    return text, persona_name, focus, boundary


def _session_voice(persona: AssistantPersona, language: str, session_id: UUID) -> str:
    """Resolve a detected language code to one concrete voice for this session.
    `persona.voices` is keyed by IETF language code for any cast role beyond the
    fixed `DEFAULT_VOICE_ROLE` anchor — an unregistered code (including the
    learner's own native language, which is never itself a key) falls back to the
    anchor. A '|'-separated value is a rotation pool: the session id picks one
    deterministically — stable within a session, varying across sessions (HVPT).
    Stateless by design: the live path never writes."""
    raw = persona.voices.get(language, persona.default_voice)
    options = [v.strip() for v in raw.split("|") if v.strip()]
    if not options:
        return persona.default_voice
    return options[session_id.int % len(options)]


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


def _compose_working_context(
    wm: WorkingMemory,
    recalled_memories: list[MemoryItem],
    selected_item: SelectedItem | None = None,
    mirror_language: Language | None = None,
    uninstalled_language: Language | None = None,
) -> tuple[str, list[Message]]:
    prompt_parts: list[str] = []
    if len(wm.available_personas) > 1:
        # Few-shot reinforcement, placed FIRST (2026-07-13, docs/PLAN.md Phase 12 live
        # testing): a plain instruction — even appended last, after the persona system
        # prompt/memory brief — reliably lost to real models narrating the switch in
        # prose instead of emitting the tag ("Sure, switching to X now!" with no
        # [PERSONA:] anywhere). A concrete correct/incorrect example didn't help either
        # while still appended last (re-tested live against aya-expanse, still 0/5).
        # Moved to the front on the theory that an 8B model attends less to instructions
        # buried after a long persona prompt/memory brief (primacy effect) — see
        # server/tests/integration/test_tutor_llm_quality_gate.py for the live check.
        persona_lines = "\n".join(f"- {p.name}" for p in wm.available_personas)
        other = next((p for p in wm.available_personas if p.id != wm.active_persona.id), None)
        instruction = (
            "Available personas:\n"
            f"{persona_lines}\n\n"
            "To switch, your reply must literally BEGIN with the tag [PERSONA:name] — the tag "
            "itself performs the switch. Describing the switch in words does nothing on its own."
        )
        if other is not None:
            instruction += (
                f'\nExample — if asked to switch to "{other.name}":\n'
                f'  Correct: "[PERSONA:{other.name}] <your first sentence as {other.name}>"\n'
                f'  Wrong:   "Sure, switching to {other.name} now! <...>" — no tag, so nothing '
                "actually switches, no matter how confidently it reads."
            )
        prompt_parts.append(instruction)
    prompt_parts.append(wm.active_persona.system_prompt)
    if wm.needs_onboarding:
        prompt_parts.insert(0, _FIRST_LAUNCH_DIRECTIVE)
        prompt_parts.append(ONBOARDING_SCRIPT)
    # Response-language instruction (FR-105/FR-113, TR-313). Three mutually exclusive
    # cases, GA-only for the first two (ProcessTurn passes both overrides as None for
    # every other persona): the user spoke an uninstalled language → answer in the
    # primary language with a re-run-the-wizard reminder; the user spoke an installed
    # language → mirror it this turn (ephemeral — nothing persisted, INV-14); otherwise
    # the persona's own configured response language.
    if uninstalled_language is not None and wm.user.primary_language is not None:
        prompt_parts.append(
            f"The user's last utterance was detected as the language with IETF code "
            f"'{uninstalled_language.code}', which is not installed on this system. Respond entirely in "
            f"the language with IETF code '{wm.user.primary_language.code}': briefly remind the user that "
            f"'{uninstalled_language.code}' is not among the installed languages and that re-running the "
            "memai-setup install wizard is how to add it, then answer their request as best you can."
        )
    elif mirror_language is not None:
        prompt_parts.append(
            f"The user is currently speaking the language with IETF code '{mirror_language.code}'. "
            "Respond in that same language."
        )
    elif wm.active_persona.response_language:
        lang = wm.active_persona.response_language
        prompt_parts.append(f"Always respond in the language with IETF code '{lang.code}'. Never switch language unless explicitly asked.")
    if wm.memory_brief:
        prompt_parts.append(wm.memory_brief.content)
    if recalled_memories:
        lines = "\n".join(f"- {_format_memory_item(m)}" for m in recalled_memories)
        prompt_parts.append(f"Relevant memories:\n{lines}")
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

    if selected_item is not None:
        # Same per-turn injection mechanism as RAG recall: a role-tagged context message,
        # inserted just before the current user turn. Item and context are injected
        # verbatim — the strategy composed them; generic code never interprets them.
        lines = [f"Work this item into the conversation naturally this turn:\n{_format_memory_item(selected_item.item)}"]
        if selected_item.context:
            lines.append(f"Context: {selected_item.context}")
        injection = Message(role="system", content="\n".join(lines))
        insert_at = len(messages) - 1 if messages else 0
        messages.insert(insert_at, injection)

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

        # Selection batches are NOT fetched here: sessions always start on GA, which has
        # no strategy — ProcessTurn fetches lazily on the first turn a strategy-bearing
        # persona (switched to mid-session) is active.
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
        language_detector: LanguageDetector,
        selection_strategies: dict[UUID, PersonaSelectionPort] | None = None,
        rolling_window_size: int = 50,
        installed_voices: dict[str, str] | None = None,
    ) -> None:
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._embedding_service = embedding_service
        self._memory_repo = memory_repo
        self._recall_detector = recall_detector
        self._persona_repo = persona_repo
        self._turn_logger = turn_logger
        self._language_detector = language_detector
        # persona_id -> selection strategy; personas without one (e.g. GA) get no batch.
        self._selection_strategies = selection_strategies or {}
        self._rolling_window_size = rolling_window_size
        # Installed-languages contract (FR-705/TR-313): language code -> that language's
        # default TTS voice. Key membership decides whether GA mirrors a detected
        # utterance language or delivers the not-installed reminder; the value picks the
        # synthesis voice when mirroring into a language the persona's own voices map
        # doesn't cover (falsy value → persona default anchor). None (tests, callers
        # predating the contract) → every supported language, no dedicated voices.
        if installed_voices is None:
            installed_voices = dict.fromkeys((lang.code for lang in SUPPORTED_LANGUAGES), "")
        self._installed_voices = installed_voices
        self._last_turn_end: float | None = None

    async def _next_selected_item(self, wm: WorkingMemory) -> SelectedItem | None:
        """Lazily fetch the active persona's selection batch on its first active turn
        (a live DB read, same standing as RAG recall), then consume one item per turn.
        Key presence in selection_batches means "already fetched" — an exhausted batch
        is not re-queried; only a [FOCUS: ...] marker replaces it."""
        if wm.needs_onboarding:
            return None
        persona_id = wm.active_persona.id
        strategy = self._selection_strategies.get(persona_id)
        if strategy is None:
            return None
        if persona_id not in wm.selection_batches:
            wm.selection_batches[persona_id] = list(await asyncio.to_thread(strategy.select_items, persona_id))
            if _TUTOR_DEBUG:
                names = [getattr(i.item, "name", getattr(i.item, "summary", "?"))
                         for i in wm.selection_batches[persona_id]]
                print(f"[tutor-debug] fetched batch for {persona_id}: {names}")
        batch = wm.selection_batches[persona_id]
        item = batch.pop(0) if batch else None
        if _TUTOR_DEBUG:
            name = getattr(item.item, "name", getattr(item.item, "summary", "?")) if item else None
            print(f"[tutor-debug] selected item this turn: {name}")
        return item

    async def execute(self, wm: WorkingMemory, audio: bytes, now: datetime) -> TurnResult | None:
        t_start = time.monotonic()
        if self._last_turn_end is not None:
            print(f"[latency] Gap since last turn: {t_start - self._last_turn_end:.1f}s")

        # 1. STT
        text, detected_language = await asyncio.to_thread(self._stt.transcribe, audio)
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
        recall = await asyncio.to_thread(self._recall_detector.detect, text)
        if recall:
            embedding = await asyncio.to_thread(self._embedding_service.embed, recall.query)
            search_results = await asyncio.to_thread(
                self._memory_repo.search,
                embedding,
                recall.memory_types,
                top_n=5,
                persona_id=wm.active_persona.id,
            )
            recalled_memories = [item for _, item in search_results]

        # 5. Persona-selected item — batch fetched lazily on the active persona's first
        # turn, then one item per turn, injected via the same context-message mechanism
        # as recall above.
        selected_item = await self._next_selected_item(wm)

        # 5b. GA response-language mirroring (FR-105/FR-113, TR-313): the GA answers in
        # whatever installed language the user just spoke; an uninstalled detected
        # language gets a primary-language reminder to re-run the wizard. Per-turn and
        # ephemeral — persona.response_language is never touched (INV-14). GA only:
        # strategy personas (e.g. the tutor, where the user deliberately speaks the
        # target language) keep their configured response language. Skipped on the
        # onboarding turn — the introduction is always in the just-selected primary.
        mirror_language: Language | None = None
        uninstalled_language: Language | None = None
        if (
            wm.active_persona.id == GENERAL_ASSISTANT_ID
            and not wm.needs_onboarding
            and detected_language is not None
        ):
            if detected_language.code in self._installed_voices:
                mirror_language = detected_language
            else:
                uninstalled_language = detected_language

        # 6. Stream the LLM response; resolve any leading [PERSONA:name]/boundary marker
        # as soon as enough tokens arrive, then synthesise each sentence via TTS as it
        # completes — instead of waiting for the entire reply before speaking a word.
        system_prompt, messages = _compose_working_context(
            wm, recalled_memories, selected_item,
            mirror_language=mirror_language, uninstalled_language=uninstalled_language,
        )
        wm.needs_onboarding = False
        response_language = wm.active_persona.response_language
        if mirror_language is not None:
            response_language = mirror_language
        elif uninstalled_language is not None and wm.user.primary_language is not None:
            response_language = wm.user.primary_language
        lang_code = response_language.code if response_language else None
        speaking_rate = wm.active_persona.speaking_rate
        # Multi-voice cast: each complete segment's OWN dominant language picks the
        # Kokoro voice — no LLM tag cooperation needed (see _session_voice — HVPT
        # rotation pools). Whole-segment granularity is deliberate: a segment that's
        # mostly the native language but quotes a target-language word stays in the
        # native voice (accented, as a real bilingual guide would sound), never split
        # mid-sentence — this is a design choice, not a detector limitation. Detection
        # only runs when there's more than one voice to pick from (a cast persona) and
        # the learner's own language is known, restricting candidates to exactly the
        # languages actually in play; a low-confidence call (see
        # infrastructure/language_detection.py) keeps whatever voice was already
        # active rather than force a switch.
        current_voice = wm.active_persona.default_voice
        # Mirroring into a language other than the persona's own (TR-313): the default
        # anchor is a voice OF the persona's language and would mangle another language's
        # pronunciation (Kokoro voices are language-specific), so resolve the mirrored
        # language's voice — the persona's registered voice for that code when one
        # exists, otherwise the installation's default voice for it.
        if mirror_language is not None and (
            wm.active_persona.response_language is None
            or mirror_language.code != wm.active_persona.response_language.code
        ):
            if mirror_language.code in wm.active_persona.voices:
                current_voice = _session_voice(wm.active_persona, mirror_language.code, wm.session_id)
            else:
                current_voice = self._installed_voices.get(mirror_language.code) or current_voice
        cast_languages = tuple(k for k in wm.active_persona.voices if k != DEFAULT_VOICE_ROLE)
        native_lang = wm.user.primary_language.code if wm.user.primary_language else None
        detection_candidates = ((native_lang,) + cast_languages) if cast_languages and native_lang else ()

        audio_chunks: list[bytes] = []
        content_parts: list[str] = []
        sentence_buffer = ""
        prefix_buffer = ""
        prefix_resolved = False
        detected_name: str | None = None
        detected_focus: str | None = None
        boundary_marker: ConversationBoundaryType | None = None
        t_first_token: float | None = None
        t_first_audio: float | None = None

        async def _synthesise_segment(text: str) -> None:
            nonlocal t_first_audio, current_voice
            processed = _spell_out_numbers(_strip_markdown(text), lang_code)
            if detection_candidates:
                detected = self._language_detector.detect(processed, detection_candidates)
                if detected is not None:
                    current_voice = _session_voice(wm.active_persona, detected, wm.session_id)
            if _TUTOR_DEBUG:
                print(f"[tutor-debug] segment voice={current_voice!r} text={processed[:60]!r}")
            content_parts.append(processed)
            t_segment_ready = time.monotonic()
            audio_chunks.append(await asyncio.to_thread(self._tts.synthesise, processed, current_voice, speaking_rate))
            if t_first_audio is None:
                t_first_audio = time.monotonic()
                print(f"[latency] TTS first chunk: {t_first_audio - t_segment_ready:.2f}s")
                print(f"[latency] Total to first audio: {t_first_audio - t_start:.2f}s")

        async def _handle_post_prefix_token(token: str) -> None:
            nonlocal sentence_buffer
            sentence_buffer += token
            complete, sentence_buffer = _split_complete_sentences(sentence_buffer)
            for sentence in complete:
                await _synthesise_segment(sentence)

        async for token in self._llm.complete(messages, system_prompt):
            if t_first_token is None:
                t_first_token = time.monotonic()
                print(f"[latency] LLM first token: {t_first_token - t_stt_done:.2f}s")

            if not prefix_resolved:
                prefix_buffer += token
                resolved = _try_resolve_prefixes(prefix_buffer, is_first_turn)
                if resolved is None:
                    continue
                token, detected_name, detected_focus, boundary_marker = resolved
                prefix_resolved = True

            await _handle_post_prefix_token(token)

        if not prefix_resolved:
            # Stream ended before the scan window closed (e.g. a short response with
            # no tag, or a genuinely dangling partial tag) — force-resolve against
            # exactly what's buffered instead of waiting for tokens that will never
            # come.
            token, detected_name, detected_focus, boundary_marker = _try_resolve_prefixes(
                prefix_buffer, is_first_turn, force=True
            )
            await _handle_post_prefix_token(token)

        if sentence_buffer.strip():
            await _synthesise_segment(sentence_buffer)

        assistant_content = "".join(content_parts).strip()
        print(f"[latency] Total turn: {time.monotonic() - t_start:.2f}s")

        # 7. Conversation boundary event — marker embedded in assistant turn below
        boundary = ConversationBoundaryDetected(boundary_type=boundary_marker) if boundary_marker else None

        # 8. Persona switch from detected prefix
        persona_switched: PersonaSwitched | None = None
        if detected_name:
            all_personas = await asyncio.to_thread(self._persona_repo.list_all)
            match = next(
                (p for p in all_personas if p.name.lower() == detected_name.lower()),
                None,
            )
            if match and match.id != wm.active_persona.id:
                persona_switched = PersonaSwitched(
                    from_persona_id=wm.active_persona.id,
                    to_persona_id=match.id,
                )
                wm.active_persona = match
                if _TUTOR_DEBUG:
                    print(f"[tutor-debug] persona switched to {match.name!r} (id={match.id})")

        # 8b. Focus marker → re-fetch the active persona's batch steered by the user's
        # expressed wish, replacing whatever remained. Resolved AFTER the persona switch
        # so a combined [PERSONA:X][FOCUS: ...] applies to X. The payload is passed
        # verbatim — only the strategy interprets it.
        if detected_focus:
            strategy = self._selection_strategies.get(wm.active_persona.id)
            if strategy is not None:
                new_batch = list(
                    await asyncio.to_thread(strategy.select_items, wm.active_persona.id, focus=detected_focus)
                )
                wm.selection_batches[wm.active_persona.id] = new_batch
                if _TUTOR_DEBUG:
                    names = [getattr(i.item, "name", getattr(i.item, "summary", "?")) for i in new_batch]
                    print(f"[tutor-debug] focus={detected_focus!r} -> re-fetched batch: {names}")

        # 9. Log assistant turn + update working memory
        # Fresh timestamp captures when the LLM finished — gap from `now` reflects response time.
        assistant_turn = Turn(timestamp=datetime.now(UTC), speaker=Speaker.ASSISTANT, content=assistant_content)
        self._turn_logger.append(wm.session_id, assistant_turn, marker=boundary_marker, persona_id=wm.active_persona.id)
        wm.recent_turns.append(assistant_turn)
        wm.total_turn_count += 1

        # 10. Rolling window check
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
