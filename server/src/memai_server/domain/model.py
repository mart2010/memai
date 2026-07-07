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
    Language("pt"), Language("ja"), Language("ko"), Language("zh-cn"),
]


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


@dataclass
class AssistantPersona:
    id: UUID
    name: str
    system_prompt: str
    languages: list[Language]  # languages this persona accepts as input; empty = primary language only
    response_language: Language  # language this persona responds in; drives TTS voice selection
    tts_voice: str              # Kokoro voice identifier, e.g. "af_heart", "ff_siwis"
    is_system: bool
    created_at: datetime
    updated_at: datetime
    speaking_rate: float = 1.0  # persona-scoped TTS rate, e.g. a language tutor may want it slower than GA
    is_active: bool = True

    def update(
        self,
        updated_at: datetime,
        name: str | None = None,
        system_prompt: str | None = None,
        tts_voice: str | None = None,
        speaking_rate: float | None = None,
        response_language: "Language | None" = None,
    ) -> None:
        if name is not None:
            self.name = name
        if system_prompt is not None:
            self.system_prompt = system_prompt
        if tts_voice is not None:
            self.tts_voice = tts_voice
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
            tts_voice=tts_voice,
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
    engagement_level: EngagementLevel = EngagementLevel.MENTIONED
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding: list[float] | None = None


@dataclass
class MemoryBrief:
    content: str
    created_at: datetime
    updated_at: datetime
