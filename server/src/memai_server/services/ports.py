# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Protocol
from uuid import UUID

from ..domain.events import ConversationBoundaryType
from ..domain.model import (
    AssistantPersona,
    Conversation,
    Concept,
    EngagementLevel,
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

# A draft is a not-yet-persisted Concept/Procedure (id=None) destined for the existing
# upsert/consolidation pipeline — the same shape PersonaEnrichmentPort.propose_items and
# the future InstallPersonaBundle (Phase 11) both emit. Episodes are never proposed.
type MemoryItemDraft = Concept | Procedure


@dataclass(frozen=True)
class SessionLine:
    """One parsed line from a JSONL session log file."""
    ts: datetime
    speaker: Speaker | None = None
    content: str | None = None
    language: Language | None = None
    marker: ConversationBoundaryType | None = None
    is_session_closed: bool = False
    clean_exit: bool = False
    persona_id: UUID | None = None


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
    def transcribe(self, audio: bytes) -> tuple[str, Language]: ...


class LLMService(Protocol):
    def complete(self, messages: list[Message], system_prompt: str) -> AsyncIterator[str]: ...


class TTSService(Protocol):
    def synthesise(self, text: str, voice: str, speed: float = 1.0) -> bytes: ...


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
    def update_persona_state(self, memory_type: MemoryType, item_id: int, persona_state: dict) -> None:
        """Persists an assessment strategy's opaque state byte-for-byte — the only write
        path to `persona_state` (single-writer contract; upserts never touch the column)."""
        ...
    def search(
        self,
        embedding: list[float],
        memory_types: tuple[MemoryType, ...],
        top_n: int,
        persona_id: UUID | None = None,
    ) -> list[tuple[float, MemoryItem]]: ...  # float is cosine similarity in [0, 1]


# ---------------------------------------------------------------------------
# Persona extension ports — persona-agnostic contracts; each persona's strategy
# implementation (Infrastructure layer) owns all persona-specific vocabulary.
# GA registers no strategies; all three ports are optional per persona.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SelectedItem:
    """`context` is free text composed by the selection strategy (e.g. an episode anchor
    or elicitation hint) — injected verbatim, never interpreted by generic code."""
    item: MemoryItem
    context: str | None = None


@dataclass(frozen=True)
class ItemAssessment:
    """`persona_state` is persisted byte-for-byte to the item's opaque slot.
    `memory_type` disambiguates the target table (concepts and procedures have
    independent id sequences)."""
    item_id: int
    memory_type: MemoryType
    persona_state: dict


class PersonaSelectionPort(Protocol):
    """Live hook — proactive, persona-driven selection (e.g. spaced repetition), distinct
    from utterance-triggered RAG recall. Fetched once per session at session start; the
    live conversation never writes to the DB, so re-querying mid-session is pointless."""
    def select_items(
        self,
        persona_id: UUID,
        category: str | None = None,
        engagement_level: EngagementLevel | None = None,
        limit: int = 10,
    ) -> Sequence[SelectedItem]: ...


class PersonaEnrichmentPort(Protocol):
    """Offline hook — proposes new memory item drafts (fed into the existing upsert
    pipeline). Exclusions are computed internally by the strategy via the repositories,
    not passed in — which items matter for exclusion is persona-specific knowledge."""
    def propose_items(self, persona_id: UUID) -> Sequence[MemoryItemDraft]: ...


class PersonaAssessmentPort(Protocol):
    """Offline hook — updates existing items' persona_state from conversational evidence.
    Dispatched by the consolidation pipeline AFTER upsert, so newly inserted items have
    ids and their first exposure is assessable."""
    def assess_items(
        self,
        persona_id: UUID,
        conversation: Conversation,
        touched_items: Sequence[MemoryItem],
    ) -> Sequence[ItemAssessment]: ...


class DisambiguationEvaluator(Protocol):
    def is_same(self, existing: MemoryItem, candidate: MemoryItem) -> bool: ...


class MemorySynthesizer(Protocol):
    def synthesize_episode(self, existing_summary: str, new_summary: str) -> str: ...
    def synthesize_concept(self, existing: Concept, new_description: str) -> str: ...
    def synthesize_procedure(self, existing: Procedure, new_description: str, new_steps: list[str]) -> tuple[str, list[str]]: ...


class PersonaRepository(Protocol):
    def get(self, persona_id: UUID) -> AssistantPersona | None: ...
    def list_all(self) -> list[AssistantPersona]: ...
    def save(self, persona: AssistantPersona) -> None: ...
    def delete(self, persona_id: UUID) -> None: ...


class MemoryBriefRepository(Protocol):
    def get(self) -> MemoryBrief | None: ...
    def save(self, brief: MemoryBrief) -> None: ...


class TurnLogger(Protocol):
    def append(self, session_id: UUID, turn: Turn, marker: ConversationBoundaryType | None = None, persona_id: UUID | None = None) -> None: ...
    def close(self, session_id: UUID, ended_at: datetime, clean_exit: bool) -> None: ...


class ConsolidationExtractor(Protocol):
    def extract(self, conversation: Conversation, primary_language: Language | None = None) -> ExtractionResult:
        """`primary_language` drives the episode-summary language rule: Episode summaries
        are always written in the user's primary language regardless of conversation
        language (Episodes are persona-independent; months of tutoring must not turn the
        user's life story into target-language documents)."""
        ...


class UnitOfWork(Protocol):
    """Demarcates a transaction boundary around a group of repository writes, so a use
    case can commit or roll them back atomically without depending on infrastructure."""
    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None: ...


