# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Protocol
from uuid import UUID

from ..domain.model import (
    AssistantPersona,
    Conversation,
    Concept,
    Episode,
    Language,
    MemoryBrief,
    MemoryType,
    Procedure,
    Turn,
    User,
)

type MemoryItem = Episode | Concept | Procedure


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
    def append(self, session_id: UUID, turn: Turn, marker: str | None = None) -> None: ...
    def close(self, session_id: UUID, ended_at: datetime, clean_exit: bool) -> None: ...


class ConsolidationExtractor(Protocol):
    def extract(self, conversation: Conversation) -> ExtractionResult: ...
