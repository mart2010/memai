# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Protocol
from uuid import UUID

from ..domain.events import ConversationBoundaryType
from ..domain.model import (
    AssistantPersona,
    Conversation,
    Concept,
    Episode,
    Language,
    MemoryBrief,
    MemoryType,
    Procedure,
    Speaker,
    Turn,
    User,
)

type MemoryItem = Episode | Concept | Procedure


@dataclass(frozen=True)
class SessionLine:
    """One parsed line from a JSONL session log file."""
    ts: datetime
    speaker: Speaker | None = None       # None for the session_closed marker line
    content: str | None = None
    language: Language | None = None
    marker: ConversationBoundaryType | None = None
    is_session_closed: bool = False
    clean_exit: bool = False


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class ExtractionResult:
    episodes: list[Episode]
    concepts: list[Concept]
    procedures: list[Procedure]


@dataclass(frozen=True)
class SessionInfo:
    session_id: UUID
    ended_at: datetime
    clean_exit: bool


class STTService(Protocol):
    def transcribe(self, audio: bytes, language_hint: Language | None) -> tuple[str, Language]: ...


class LLMService(Protocol):
    def complete(self, messages: list[Message], system_prompt: str) -> AsyncIterator[str]: ...


class TTSService(Protocol):
    def synthesise(self, text: str) -> bytes: ...


class EmbeddingService(Protocol):
    def embed(self, text: str) -> list[float]: ...


class UserRepository(Protocol):
    def get(self) -> User | None: ...
    def save(self, user: User) -> None: ...


class SessionLogReader(Protocol):
    """Reads session metadata and turn tail from flat JSONL log files."""
    def get_previous(self) -> SessionInfo | None: ...
    def read_tail(self, session_id: UUID, max_turns: int) -> list[Turn]: ...


class ConversationRepository(Protocol):
    def save_new(self, conversation: Conversation, session_id: UUID) -> int: ...
    def save_consolidation(self, conversation: Conversation) -> None: ...
    def get_unconsolidated(self) -> list[Conversation]: ...
    def is_session_persisted(self, session_id: UUID) -> bool: ...
    def get_last_open_id(self) -> int | None: ...
    def extend_conversation(self, conversation_id: int, session_id: UUID, turns: list[Turn], ended_at: datetime | None) -> None: ...


class SessionReplayReader(Protocol):
    """Walks session log files newest-first, stops at the first already-persisted
    session (monotonic invariant), and returns unprocessed sessions oldest-first."""
    def get_unprocessed(
        self,
        is_persisted: Callable[[UUID], bool],
    ) -> list[tuple[UUID, list[SessionLine]]]: ...


class MemoryRepository(Protocol):
    def upsert_episode(self, episode: Episode) -> int: ...
    def upsert_concept(self, concept: Concept) -> int: ...
    def upsert_procedure(self, procedure: Procedure) -> int: ...
    def search(
        self,
        embedding: list[float],
        memory_types: tuple[MemoryType, ...],
        top_n: int,
        persona_id: UUID | None = None,
    ) -> list[MemoryItem]: ...


class PersonaRepository(Protocol):
    def get(self, persona_id: UUID) -> AssistantPersona | None: ...
    def list_all(self) -> list[AssistantPersona]: ...
    def save(self, persona: AssistantPersona) -> None: ...
    def delete(self, persona_id: UUID) -> None: ...


class MemoryBriefRepository(Protocol):
    def get(self) -> MemoryBrief | None: ...
    def save(self, brief: MemoryBrief) -> None: ...


class TurnLogger(Protocol):
    def append(self, session_id: UUID, turn: Turn, marker: ConversationBoundaryType | None = None) -> None: ...
    def close(self, session_id: UUID, ended_at: datetime, clean_exit: bool) -> None: ...


class ConsolidationExtractor(Protocol):
    def extract(self, conversation: Conversation) -> ExtractionResult: ...
