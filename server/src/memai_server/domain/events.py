# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from .model import Language, MemoryType


@dataclass(frozen=True)
class PrimaryLanguageChanged:
    user_id: UUID
    old_language: Language | None  # None when language was not set before (post-onboarding update)
    new_language: Language


class RecallSource(Enum):
    USER = "user"          # explicit recall intent in user speech ("remember when…")
    DOCUMENT = "document"  # user references previously injected document material


@dataclass(frozen=True)
class RecallTriggered:
    query: str
    memory_types: tuple[MemoryType, ...]  # empty tuple means all types
    source: RecallSource = RecallSource.USER

    def __post_init__(self) -> None:
        if not self.query or not self.query.strip():
            raise ValueError("RecallTriggered query must not be empty")


@dataclass(frozen=True)
class PersonaSwitched:
    from_persona_id: UUID
    to_persona_id: UUID


@dataclass(frozen=True)
class PersonaDeactivated:
    persona_id: UUID


@dataclass(frozen=True)
class PersonaReactivated:
    persona_id: UUID


class ConversationBoundaryType(Enum):
    BREAK = "break"
    CONTINUATION = "continuation"


@dataclass(frozen=True)
class ConversationBoundaryDetected:
    boundary_type: ConversationBoundaryType
