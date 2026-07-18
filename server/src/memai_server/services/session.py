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
    AssistantPersona,
    Concept,
    MemoryBrief,
    MemoryType,
    Speaker,
    Turn,
    User,
    cosine_similarity,
)
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
    RecallGate,
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
    # Directive concepts (FR-207) GA can act on this session — persona-switch targets
    # today. Small, stable set; fetched once at session start, same posture as
    # memory_brief below.
    directive_concepts: list[Concept]
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
    # RecallGate cache (FR-309/TR-314), keyed by persona id: every (embedding, results)
    # pair from an utterance that actually triggered a real memory search for that
    # persona this session, oldest first — persona-keyed because memory search is
    # itself persona-scoped, so a GA-context embedding is not a meaningful comparison
    # for a tutor-context one. The *whole* history is kept and compared against, not
    # just the last entry, because nothing new can enter long-term memory mid-session
    # (INV-1): the searchable set is frozen for the conversation's duration, so a
    # repeat of any earlier query — not only the immediately preceding one — would
    # deterministically return the same results again. Absent/empty key = no search
    # has happened yet for that persona.
    recall_history: dict[UUID, list[tuple[list[float], list[MemoryItem]]]] = field(default_factory=dict)


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


# How much lead-in prose to buffer while scanning for a [FOCUS:] tag appearing
# anywhere in a streaming response (not just position zero) before giving up on
# finding one. Widened from a position-zero-only check after live testing (see
# docs/PLAN.md Phase 12 live smoke) showed real models routinely preface a tag-bearing
# reply with conversational lead-in (an apology, an acknowledgment). This is a
# latency/reliability tradeoff, not a free win: a turn with no tag at all now waits up
# to this many characters (or stream end, whichever comes first) before speech can
# start, instead of usually resolving within the first token. Value is a placeholder
# pending real tuning against live turns, same posture as the 0.93/0.75 upsert
# thresholds. (Persona switching no longer uses this scan at all — FR-207 decides
# before the LLM is even called.)
_PREFIX_SCAN_WINDOW_CHARS = 200

# Directive matching (FR-207): minimum cosine similarity between a turn's own
# utterance and a Directive concept's embedded canonical phrasing to execute that
# directive's action. Value is a placeholder pending real tuning against live turns,
# same posture as _PREFIX_SCAN_WINDOW_CHARS/the 0.93/0.75 upsert thresholds — err
# toward requiring a close match, since a false positive silently switches personas.
_DIRECTIVE_MATCH_THRESHOLD = 0.85


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
) -> tuple[str, str | None, ConversationBoundaryType | None] | None:
    """Resolve the optional response-prefix marker [FOCUS: wish] from anywhere within
    the first `_PREFIX_SCAN_WINDOW_CHARS` characters of a streaming LLM response — not
    just position zero, see that constant's docstring — followed by an optional
    boundary marker at the very start of whatever remains.

    Returns None while more tokens might change the outcome (still inside the scan
    window with a tag not yet found, or the tail of `buffer` might be an in-progress
    tag) — unless `force` is set, which finalizes against exactly what's buffered now
    (used once the LLM stream itself has ended and no further tokens are coming, so
    nothing left unresolved can ever resolve). Otherwise returns (remaining_text,
    focus_or_None, boundary_type_or_None).
    """
    remaining = buffer
    focus: str | None = None

    extracted = _extract_tag(remaining, "[FOCUS:")
    if extracted is not None:
        remaining, focus = extracted

    if not force:
        still_waiting = (
            _tag_might_still_open(remaining, "[FOCUS:") if focus is None else False
        ) or (focus is None and len(remaining) < _PREFIX_SCAN_WINDOW_CHARS)
        if still_waiting:
            return None

    text = remaining.lstrip()
    boundary_result = _resolve_boundary_marker(text, is_first_turn)
    if boundary_result is None:
        if not force:
            return None
        boundary_result = (text, None)  # dangling partial boundary marker at stream end — plain text
    text, boundary = boundary_result
    return text, focus, boundary


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


# User turns are rendered to the LLM with a [lang:code] prefix (FR-114/TR-303), so a
# model may mimic the convention in its own output — strip any such tag before TTS,
# the same never-spoken rule as every other bracket marker.
_LANG_TAG = re.compile(r"\[lang:[^\]]{0,20}\]\s*", re.IGNORECASE)


def _strip_markdown(text: str) -> str:
    """Drop markdown headers/rules/emphasis/code markers, emoji, and mimicked
    [lang:] tags — LLMs emit them despite instructions not to, and TTS either reads
    them aloud literally (e.g. "hash", "asterisk") or renders them as
    unpronounceable glyphs."""
    text = _LANG_TAG.sub("", text)
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


def _render_turn_content(turn: Turn) -> str:
    """User turns carry their STT-detected language into the LLM context as a
    [lang:code] prefix (FR-114): during a tutor session it tells the model whether
    the learner produced the target language, asked in their own, or — a third
    language tag — likely stumbled on pronunciation. Rendering-only: stored turn
    content and session logs stay clean (the log's "language" field already carries
    the code)."""
    if turn.speaker == Speaker.USER and turn.language is not None:
        return f"[lang:{turn.language.code}] {turn.content}"
    return turn.content


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
) -> tuple[str, list[Message]]:
    # No persona-listing/switch-instruction block here (FR-202/FR-203 retired, FR-207):
    # switching is now decided deterministically before this function ever runs (see
    # ProcessTurn.execute step 3b), so the system prompt never names another persona —
    # the fix for the language-drift this scaffolding used to cause.
    prompt_parts: list[str] = [wm.active_persona.system_prompt]
    if wm.needs_onboarding:
        prompt_parts.insert(0, _FIRST_LAUNCH_DIRECTIVE)
        prompt_parts.append(ONBOARDING_SCRIPT)
    # Response-language instruction (FR-105): the active persona's fixed response
    # language, GA included (its response_language is set to User.primary_language at
    # onboarding and only changes on explicit request, INV-14).
    if wm.active_persona.response_language and not any(
        k != DEFAULT_VOICE_ROLE for k in wm.active_persona.voices
    ):
        # Suppressed for cast personas (non-default voices keys): a two-teacher cast
        # deliberately speaks two languages per reply, and a "respond only in X"
        # directive would fight the persona's own prompt (which owns language use).
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
        tail_text = "\n".join(f"{t.speaker.value}: {_render_turn_content(t)}" for t in wm.session_tail)
        messages.append(Message(role="system", content=f"Tail of previous session:\n{tail_text}"))

    if wm.rolling_summary:
        messages.append(Message(
            role="system",
            content=f"Earlier in this conversation: {wm.rolling_summary}",
        ))
    for turn in wm.recent_turns:
        role = "user" if turn.speaker == Speaker.USER else "assistant"
        messages.append(Message(role=role, content=_render_turn_content(turn)))

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
        memory_repo: MemoryRepository,
        session_tail_turns: int = 10,
        session_continuation_threshold_hours: float = 24.0,
    ) -> None:
        self._user_repo = user_repo
        self._persona_repo = persona_repo
        self._memory_brief_repo = memory_brief_repo
        self._session_log_reader = session_log_reader
        self._memory_repo = memory_repo
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
            directive_concepts=self._memory_repo.list_directives(GENERAL_ASSISTANT_ID),
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
        default_recall_gate: RecallGate,
        persona_repo: PersonaRepository,
        turn_logger: TurnLogger,
        language_detector: LanguageDetector,
        selection_strategies: dict[UUID, PersonaSelectionPort] | None = None,
        recall_gates: dict[UUID, RecallGate] | None = None,
        rolling_window_size: int = 50,
    ) -> None:
        # Local import: .persona also imports WorkingMemory from this module, so a
        # top-level import here would be circular (same pattern as _format_memory_item's
        # local domain.model import above).
        from .persona import SwitchPersona

        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._embedding_service = embedding_service
        self._memory_repo = memory_repo
        self._persona_repo = persona_repo
        self._turn_logger = turn_logger
        self._language_detector = language_detector
        self._switch_persona = SwitchPersona(persona_repo)
        # persona_id -> selection strategy; personas without one (e.g. GA) get no batch.
        self._selection_strategies = selection_strategies or {}
        # persona_id -> RecallGate override (e.g. the tutor's); every other persona,
        # GA included, falls back to default_recall_gate (FR-309/TR-314) — unlike
        # selection_strategies above, recall gating is never a no-op.
        self._recall_gates = recall_gates or {}
        self._default_recall_gate = default_recall_gate
        self._rolling_window_size = rolling_window_size
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

        # 3b. Directive matching (FR-207) — replaces the retired [PERSONA:] tag scheme
        # (FR-202/FR-203). A small, fixed set of GA-owned Concepts (persona-switch
        # targets, today) checked every turn, unconditionally: a directive phrase is a
        # short command, exactly what RecallGate.should_embed is designed to skip as
        # trivial, so this runs independent of the recall gate below (FR-309/TR-314
        # untouched — a second, separate embed call on turns where recall also embeds,
        # accepted as the cost of keeping the two systems decoupled). A clearing match
        # executes deterministically, before this turn's system prompt is composed —
        # no LLM decision or system-prompt scaffolding involved, which is the actual
        # fix for the GA language drift that scaffolding used to cause.
        persona_switched: PersonaSwitched | None = None
        if wm.directive_concepts:
            directive_embedding = await asyncio.to_thread(self._embedding_service.embed, text)
            best_similarity, best_directive = max(
                (
                    (cosine_similarity(directive_embedding, d.embedding), d)
                    for d in wm.directive_concepts
                    if d.embedding is not None
                ),
                key=lambda pair: pair[0],
                default=(0.0, None),
            )
            if best_similarity >= _DIRECTIVE_MATCH_THRESHOLD and best_directive is not None:
                action = (best_directive.directive or {}).get("action")
                if action == "switch_persona":
                    target_id = UUID((best_directive.directive or {})["target_persona_id"])
                    if target_id != wm.active_persona.id:  # already-active target: silent no-op
                        persona_switched = self._switch_persona.execute(wm, target_id)
                        if _TUTOR_DEBUG:
                            print(f"[tutor-debug] directive matched (similarity={best_similarity:.2f}) -> switched to {wm.active_persona.name!r}")

        # 4. Recall gate → enrich working context from LTM (FR-309/TR-314). Replaces the
        # old per-turn LLM classification call with a persona-scoped, local threshold
        # policy: should_embed() short-circuits trivial utterances before any embedding
        # is computed; should_search() then skips the DB round trip when this turn's
        # embedding is nearly identical to ANY prior search this session (not just the
        # last one — nothing new can enter memory mid-session, INV-1, so a repeat of
        # any earlier query would return the same thing again), reusing that prior
        # search's cached results instead. Skipped entirely on a turn that just switched
        # persona (3b) — recall/selection resume normally next turn, under the new
        # persona; this turn's own utterance was a directive, not a content question.
        recalled_memories: list[MemoryItem] = []
        persona_id = wm.active_persona.id
        if persona_switched is None:
            gate = self._recall_gates.get(persona_id, self._default_recall_gate)
            if gate.should_embed(text):
                embedding = await asyncio.to_thread(self._embedding_service.embed, text)
                history = wm.recall_history.get(persona_id, [])
                if history:
                    similarities = (
                        (cosine_similarity(embedding, past_embedding), past_results)
                        for past_embedding, past_results in history
                    )
                    max_similarity, best_match = max(similarities, key=lambda pair: pair[0])
                else:
                    max_similarity, best_match = None, []
                if gate.should_search(max_similarity):
                    search_results = await asyncio.to_thread(
                        self._memory_repo.search,
                        embedding,
                        tuple(MemoryType),
                        top_n=5,
                        persona_id=persona_id,
                    )
                    recalled_memories = [item for _, item in search_results]
                    wm.recall_history.setdefault(persona_id, []).append((embedding, recalled_memories))
                else:
                    recalled_memories = best_match

        # 5. Persona-selected item — batch fetched lazily on the active persona's first
        # turn, then one item per turn, injected via the same context-message mechanism
        # as recall above. Skipped on a switch turn for the same reason as step 4.
        selected_item = await self._next_selected_item(wm) if persona_switched is None else None

        # 6. Stream the LLM response; resolve any leading [FOCUS:]/boundary marker as
        # soon as enough tokens arrive, then synthesise each sentence via TTS as it
        # completes — instead of waiting for the entire reply before speaking a word.
        system_prompt, messages = _compose_working_context(wm, recalled_memories, selected_item)
        wm.needs_onboarding = False
        response_language = wm.active_persona.response_language
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
        cast_languages = tuple(k for k in wm.active_persona.voices if k != DEFAULT_VOICE_ROLE)
        native_lang = wm.user.primary_language.code if wm.user.primary_language else None
        detection_candidates = ((native_lang,) + cast_languages) if cast_languages and native_lang else ()

        audio_chunks: list[bytes] = []
        content_parts: list[str] = []
        sentence_buffer = ""
        prefix_buffer = ""
        prefix_resolved = False
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
                token, detected_focus, boundary_marker = resolved
                prefix_resolved = True

            await _handle_post_prefix_token(token)

        if not prefix_resolved:
            # Stream ended before the scan window closed (e.g. a short response with
            # no tag, or a genuinely dangling partial tag) — force-resolve against
            # exactly what's buffered instead of waiting for tokens that will never
            # come.
            token, detected_focus, boundary_marker = _try_resolve_prefixes(
                prefix_buffer, is_first_turn, force=True
            )
            await _handle_post_prefix_token(token)

        if sentence_buffer.strip():
            await _synthesise_segment(sentence_buffer)

        assistant_content = "".join(content_parts).strip()
        print(f"[latency] Total turn: {time.monotonic() - t_start:.2f}s")

        # 7. Conversation boundary event — marker embedded in assistant turn below
        boundary = ConversationBoundaryDetected(boundary_type=boundary_marker) if boundary_marker else None

        # 8. Focus marker → re-fetch the active persona's batch steered by the user's
        # expressed wish, replacing whatever remained. wm.active_persona already
        # reflects any directive switch from step 3b, so a combined switch+focus turn
        # (e.g. "switch to my tutor, let's just review vocabulary") steers the NEW
        # persona's batch. The payload is passed verbatim — only the strategy
        # interprets it.
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
