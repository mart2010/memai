# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from .model import Language


@dataclass(frozen=True)
class PrimaryLanguageChanged:
    user_id: UUID
    old_language: Language | None  # None when language was not set before (post-onboarding update)
    new_language: Language


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
