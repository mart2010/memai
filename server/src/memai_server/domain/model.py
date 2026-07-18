# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum, IntEnum
from uuid import UUID


# ---------------------------------------------------------------------------
# Value Objects & Enums
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Language:
    code: str  # IETF language tag, e.g. "en", "fr", "de", "zh-Hans"

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ValueError("Language code cannot be empty")


# Intersection of faster-whisper (~99 languages) and Kokoro (~9 languages).
# Kokoro is the limiting factor; this is the full set Memai supports.
SUPPORTED_LANGUAGES: list[Language] = [
    Language("en"), Language("fr"), Language("es"), Language("it"),
    Language("pt"), Language("ja"), Language("zh-cn"),
]


def resolve_installed_languages(installed_codes: tuple[str, ...] | list[str]) -> list[Language]:
    """The installed languages (FR-705): the wizard-selected codes intersected with
    SUPPORTED_LANGUAGES, in SUPPORTED_LANGUAGES order. Empty codes (config predates
    the [languages] key) → everything supported. Unsupported codes are silently
    dropped here — callers that want to warn or fail compare the result themselves."""
    if not installed_codes:
        return list(SUPPORTED_LANGUAGES)
    return [lang for lang in SUPPORTED_LANGUAGES if lang.code in installed_codes]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure Python cosine similarity in [-1, 1] between two embedding vectors —
    used by RecallGate.should_search (FR-309/TR-314) to compare a turn's embedding
    against the last one that actually triggered a memory search this session,
    entirely in-process (no DB round trip; pgvector's own `<=>` operator is a
    separate, unrelated comparison against stored items). Does not assume
    L2-normalised input, unlike a plain dot product, so it stays correct against
    any EmbeddingService implementation, real or fake. Returns 0.0 for a
    zero-magnitude vector rather than dividing by zero."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class EngagementLevel(IntEnum):
    UNSEEN = 0
    MENTIONED = 1
    EXPLORED = 2
    INTEGRATED = 3


class MemoryType(Enum):
    EPISODE = "episode"
    CONCEPT = "concept"
    PROCEDURE = "procedure"


class Speaker(Enum):
    USER = "user"
    ASSISTANT = "assistant"



# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

GENERAL_ASSISTANT_ID = UUID("00000000-0000-0000-0000-000000000001")

# Every persona's `voices` map must carry this key. Other keys (e.g. a language
# tutor's two-teacher cast) are IETF language codes, persona-defined; generic code
# only ever reads DEFAULT_VOICE_ROLE by name — any other key is resolved by the
# language actually detected in a synthesized segment (see `_session_voice` in
# services/session.py), never by generic code branching on the key itself.
# A non-default key's value may be a "|"-separated rotation pool ("ef_dora|em_alex"),
# resolved to ONE voice per session by the live path (HVPT — multi-voice exposure).
# The default key is the fixed anchor and must always be a single voice.
DEFAULT_VOICE_ROLE = "default"


def _validate_voices(voices: dict[str, str]) -> None:
    if DEFAULT_VOICE_ROLE not in voices:
        raise ValueError(f"voices must include the '{DEFAULT_VOICE_ROLE}' role")
    if "|" in voices[DEFAULT_VOICE_ROLE]:
        raise ValueError(
            f"the '{DEFAULT_VOICE_ROLE}' voice must be a single voice (the fixed anchor) — "
            "'|' rotation pools are only for non-default (language-code) keys"
        )


@dataclass
class AssistantPersona:
    id: UUID
    name: str
    system_prompt: str
    # The session language pair(s): input languages expected while this persona is
    # active (a tutor's bundle target list + the primary language, appended at install).
    # Empty = no restriction (the GA accepts any installed language).
    languages: list[Language]
    response_language: Language  # language this persona responds in; drives TTS voice selection
    voices: dict[str, str]      # IETF language code (or DEFAULT_VOICE_ROLE) -> Kokoro voice identifier
    is_system: bool
    created_at: datetime
    updated_at: datetime
    speaking_rate: float = 1.0  # persona-scoped TTS rate, e.g. a language tutor may want it slower than GA
    is_active: bool = True
    # Author-namespaced bundle identity (e.g. "meo/spanish-tutor"), unique by convention;
    # None for GA and user-created personas. Set once at bundle install, never reassigned.
    persona_key: str | None = None
    # Names the strategy set (selection/assessment/enrichment) this persona binds to,
    # e.g. "language_tutor". Resolved against the composition root's registry at startup;
    # None (GA, user-created personas) binds nothing. Set at creation like persona_key.
    strategy: str | None = None
    # Opaque persona-owned tunables, copied verbatim from a bundle's [persona.settings].
    # Read only by the owning persona's own strategies; generic code never branches on
    # its contents (same leak-prevention contract as persona_state, one level up).
    settings: dict | None = None

    def __post_init__(self) -> None:
        _validate_voices(self.voices)

    @property
    def default_voice(self) -> str:
        return self.voices[DEFAULT_VOICE_ROLE]

    def update(
        self,
        updated_at: datetime,
        name: str | None = None,
        system_prompt: str | None = None,
        voices: dict[str, str] | None = None,
        speaking_rate: float | None = None,
        response_language: "Language | None" = None,
    ) -> None:
        if name is not None:
            self.name = name
        if system_prompt is not None:
            self.system_prompt = system_prompt
        if voices is not None:
            _validate_voices(voices)
            self.voices = voices
        if speaking_rate is not None:
            self.speaking_rate = speaking_rate
        if response_language is not None:
            self.response_language = response_language
        self.updated_at = updated_at

    def deactivate(self, updated_at: datetime) -> None:
        if self.is_system:
            raise ValueError("System personas cannot be deactivated")
        self.is_active = False
        self.updated_at = updated_at

    def reactivate(self, updated_at: datetime) -> None:
        self.is_active = True
        self.updated_at = updated_at

    @classmethod
    def general_assistant(
        cls,
        system_prompt: str,
        response_language: "Language" = Language("en"),
        tts_voice: str = "af_heart",
    ) -> "AssistantPersona":
        now = datetime.now(UTC)
        return cls(
            id=GENERAL_ASSISTANT_ID,
            name="Vocal Assistant",  # generic placeholder — Memai is the product, not the persona's name
            system_prompt=system_prompt,
            languages=[],
            response_language=response_language,
            voices={DEFAULT_VOICE_ROLE: tts_voice},
            is_system=True,
            created_at=now,
            updated_at=now,
        )


@dataclass
class User:
    id: UUID
    primary_language: Language | None = None  # None until onboarding is complete
    secondary_languages: list[Language] = field(default_factory=list)
    idle_consolidation_minutes: float = 5.0  # how long to wait after disconnect before running offline consolidation

    def update_primary_language(self, new_language: Language) -> None:
        self.primary_language = new_language

    def update_idle_consolidation_minutes(self, minutes: float) -> None:
        self.idle_consolidation_minutes = minutes


@dataclass
class Turn:
    timestamp: datetime
    speaker: Speaker
    content: str
    language: Language | None = None  # set from STT output


@dataclass
class Conversation:
    id: int | None
    started_at: datetime
    persona_id: UUID
    turns: list[Turn] = field(default_factory=list)
    ended_at: datetime | None = None
    worthiness: bool | None = None
    summary: str | None = None
    consolidated: bool = False

    def add_turn(self, turn: Turn) -> None:
        if self.ended_at:
            raise ValueError("Cannot add a turn to an ended Conversation")
        self.turns.append(turn)

    def end(self, ended_at: datetime) -> None:
        self.ended_at = ended_at

    def mark_consolidated(self, worthiness: bool, summary: str | None) -> None:
        if self.consolidated:
            raise ValueError("Conversation is already consolidated")
        if not self.ended_at:
            raise ValueError("Cannot consolidate an active Conversation")
        if not self.turns:
            raise ValueError("Cannot consolidate a Conversation with no turns")
        self.worthiness = worthiness
        self.summary = summary
        self.consolidated = True

    @property
    def is_eligible_for_consolidation(self) -> bool:
        return bool(self.ended_at and self.turns and not self.consolidated)


@dataclass
class Episode:
    id: int | None
    summary: str
    happened_at: datetime
    origin_conversation_id: int  # provenance only — where this episode was first extracted
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding: list[float] | None = None


@dataclass
class Concept:
    id: int | None
    persona_id: UUID
    name: str
    description: str
    language: Language  # first introduced; stays fixed on upsert
    category: str | None = None  # free text, interpreted in the owning persona's own vocabulary
    # Opaque, unkeyed slot (persona_id already scopes ownership). Single-writer contract:
    # written only by the owning persona's assessment strategy, read only by that persona's
    # selection strategy — generic code never branches on its contents.
    persona_state: dict | None = None
    # A Directive (FR-207): None for an ordinary concept; populated marks this a
    # GA-owned, generic-code-actionable concept, e.g. {"action": "switch_persona",
    # "target_persona_id": "<uuid str>"}. The one Concept field generic code IS meant
    # to read and act on — the deliberate opposite of persona_state/settings' opacity
    # contract (INV-6).
    directive: dict | None = None
    engagement_level: EngagementLevel = EngagementLevel.MENTIONED
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding: list[float] | None = None


@dataclass
class Procedure:
    id: int | None
    persona_id: UUID
    name: str
    description: str  # primary carrier of knowledge; always populated (~300 words)
    language: Language  # first introduced; stays fixed on upsert
    steps: list[str] = field(default_factory=list)  # empty when not decomposable into discrete steps
    category: str | None = None  # free text, interpreted in the owning persona's own vocabulary
    persona_state: dict | None = None  # same single-writer contract as Concept.persona_state
    engagement_level: EngagementLevel = EngagementLevel.MENTIONED
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding: list[float] | None = None


@dataclass
class MemoryBrief:
    content: str
    created_at: datetime
    updated_at: datetime
