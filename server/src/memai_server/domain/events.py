from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from .model import Language, MemoryType


@dataclass(frozen=True)
class PrimaryLanguageChanged:
    user_id: UUID
    old_language: Language
    new_language: Language


@dataclass(frozen=True)
class RecallTriggered:
    query: str
    memory_types: tuple[MemoryType, ...]  # empty tuple means all types

    def __post_init__(self) -> None:
        if not self.query or not self.query.strip():
            raise ValueError("RecallTriggered query must not be empty")


@dataclass(frozen=True)
class PersonaSwitched:
    from_persona_id: UUID
    to_persona_id: UUID


class BoundaryType(Enum):
    BREAK = "break"
    CONTINUATION = "continuation"


@dataclass(frozen=True)
class ConversationBoundaryDetected:
    boundary_type: BoundaryType
