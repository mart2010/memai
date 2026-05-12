from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Protocol
from uuid import UUID

from ..domain.model import (
    AssistantPersona,
    ConversationRecord,
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


class STTService(Protocol):
    def transcribe(self, audio: bytes, language_hint: Language) -> tuple[str, Language]: ...


class LLMService(Protocol):
    def complete(self, messages: list[Message], system_prompt: str) -> AsyncIterator[str]: ...


class TTSService(Protocol):
    def synthesise(self, text: str) -> bytes: ...


class EmbeddingService(Protocol):
    def embed(self, text: str) -> list[float]: ...


class UserRepository(Protocol):
    def get(self) -> User | None: ...
    def save(self, user: User) -> None: ...


class ConversationRepository(Protocol):
    def save(self, record: ConversationRecord) -> None: ...
    def get_unconsolidated(self) -> list[ConversationRecord]: ...


class MemoryRepository(Protocol):
    def upsert_episode(self, episode: Episode) -> None: ...
    def upsert_concept(self, concept: Concept) -> None: ...
    def upsert_procedure(self, procedure: Procedure) -> None: ...
    def search(
        self,
        embedding: list[float],
        memory_types: tuple[MemoryType, ...],
        top_n: int,
    ) -> list[MemoryItem]: ...


class PersonaRepository(Protocol):
    def get(self, persona_id: UUID) -> AssistantPersona | None: ...
    def list_all(self) -> list[AssistantPersona]: ...
    def find_by_language(self, language: Language) -> AssistantPersona | None: ...
    def save(self, persona: AssistantPersona) -> None: ...
    def delete(self, persona_id: UUID) -> None: ...


class MemoryBriefRepository(Protocol):
    def get(self) -> MemoryBrief | None: ...
    def save(self, brief: MemoryBrief) -> None: ...


class TurnLogger(Protocol):
    def append(self, session_id: UUID, turn: Turn) -> None: ...
    def close(self, session_id: UUID, ended_at: datetime) -> None: ...


class ConsolidationExtractor(Protocol):
    def extract(self, record: ConversationRecord) -> ExtractionResult: ...
