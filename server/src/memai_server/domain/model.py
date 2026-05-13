from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
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


class EngagementLevel(Enum):
    MENTIONED = "mentioned"
    EXPLORED = "explored"
    PRACTICED = "practiced"
    INTEGRATED = "integrated"


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
    languages: list[Language]  # languages this persona operates in; empty = primary language only
    is_system: bool
    created_at: datetime
    updated_at: datetime

    def update(self, updated_at: datetime, name: str | None = None, system_prompt: str | None = None) -> None:
        if self.is_system:
            raise ValueError("System personas cannot be modified")
        if name is not None:
            self.name = name
        if system_prompt is not None:
            self.system_prompt = system_prompt
        self.updated_at = updated_at

    @classmethod
    def general_assistant(cls, system_prompt: str) -> "AssistantPersona":
        now = datetime.now(UTC)
        return cls(
            id=GENERAL_ASSISTANT_ID,
            name="General Assistant",
            system_prompt=system_prompt,
            languages=[],
            is_system=True,
            created_at=now,
            updated_at=now,
        )


@dataclass
class User:
    id: UUID
    primary_language: Language | None = None  # None until onboarding is complete
    secondary_languages: list[Language] = field(default_factory=list)

    def update_primary_language(self, new_language: Language) -> None:
        self.primary_language = new_language


@dataclass
class Turn:
    timestamp: datetime
    speaker: Speaker
    content: str
    language: Language | None = None  # set from STT output


@dataclass
class LiveConversation:
    started_at: datetime
    persona_id: UUID
    recent_turns: list[Turn] = field(default_factory=list)
    rolling_summary: str | None = None
    total_turn_count: int = 0

    def add_turn(self, turn: Turn) -> None:
        self.recent_turns.append(turn)
        self.total_turn_count += 1

    def apply_rolling_summary(self, summary: str, turns_summarised: int) -> None:
        """Replace oldest turns with a compact summary block."""
        self.rolling_summary = summary
        self.recent_turns = self.recent_turns[turns_summarised:]


@dataclass
class ConversationRecord:
    id: UUID
    started_at: datetime
    persona_snapshot: AssistantPersona
    turns: list[Turn] = field(default_factory=list)
    ended_at: datetime | None = None
    worthiness: bool | None = None
    summary: str | None = None
    consolidated: bool = False

    def add_turn(self, turn: Turn) -> None:
        if self.ended_at:
            raise ValueError("Cannot add a turn to an ended ConversationRecord")
        self.turns.append(turn)

    def end(self, ended_at: datetime) -> None:
        self.ended_at = ended_at

    def mark_consolidated(self, worthiness: bool, summary: str | None) -> None:
        if self.consolidated:
            raise ValueError("ConversationRecord is already consolidated")
        if not self.ended_at:
            raise ValueError("Cannot consolidate an active ConversationRecord")
        if not self.turns:
            raise ValueError("Cannot consolidate a ConversationRecord with no turns")
        self.worthiness = worthiness
        self.summary = summary
        self.consolidated = True

    @property
    def is_eligible_for_consolidation(self) -> bool:
        return bool(self.ended_at and self.turns and not self.consolidated)


@dataclass
class Episode:
    id: UUID
    summary: str
    happened_at: datetime
    conversation_id: UUID
    embedding: list[float] | None = None


@dataclass
class Concept:
    id: UUID
    name: str
    description: str
    language: Language
    engagement_level: EngagementLevel = EngagementLevel.MENTIONED
    embedding: list[float] | None = None

    def update_engagement(self, new_level: EngagementLevel) -> None:
        self.engagement_level = new_level


@dataclass
class Procedure:
    id: UUID
    name: str
    steps: list[str]
    language: Language
    engagement_level: EngagementLevel = EngagementLevel.MENTIONED
    embedding: list[float] | None = None

    def update_engagement(self, new_level: EngagementLevel) -> None:
        self.engagement_level = new_level


@dataclass
class MemoryBrief:
    content: str
    generated_at: datetime


