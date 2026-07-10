from datetime import datetime, UTC
from uuid import UUID

from memai_server.domain.events import ConversationBoundaryType, RecallTriggered
from memai_server.domain.model import (
    AssistantPersona,
    Conversation,
    Concept,
    Episode,
    EngagementLevel,
    Language,
    MemoryBrief,
    MemoryType,
    Procedure,
    Turn,
    User,
)
from memai_server.services.ports import (
    ConsolidationExtractor,
    ExtractionResult,
    ItemAssessment,
    MemoryItem,
    MemoryItemDraft,
    Message,
    SelectedItem,
    SessionInfo,
    SessionLine,
)


# ---------------------------------------------------------------------------
# Infrastructure fakes
# ---------------------------------------------------------------------------

class FakeSTTService:
    def __init__(self, transcript: str = "hello", language: Language = Language("en")) -> None:
        self.transcript = transcript
        self.language = language
        self.calls: list[bytes] = []

    def transcribe(self, audio: bytes) -> tuple[str, Language]:
        self.calls.append(audio)
        return self.transcript, self.language


class FakeLLMService:
    def __init__(self, response: str = "Understood.") -> None:
        self.response = response
        self.calls: list[tuple[list[Message], str]] = []

    async def complete(self, messages: list[Message], system_prompt: str):
        self.calls.append((messages, system_prompt))
        for word in self.response.split(" "):
            yield word + " "


class FakeTTSService:
    def __init__(self, audio: bytes = b"audio") -> None:
        self.audio = audio
        self.synthesised: list[tuple[str, str, float]] = []

    def synthesise(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        self.synthesised.append((text, voice, speed))
        return self.audio


class FakeEmbeddingService:
    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector or [0.1] * 8
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return self.vector


class FakeUserRepository:
    def __init__(self, user: User | None = None) -> None:
        self._user = user

    def get(self) -> User | None:
        return self._user

    def save(self, user: User) -> None:
        self._user = user


class FakeSessionLogReader:
    def __init__(
        self,
        previous: SessionInfo | None = None,
        tail: list[Turn] | None = None,
    ) -> None:
        self._previous = previous
        self._tail = tail or []

    def get_previous(self) -> SessionInfo | None:
        return self._previous

    def read_tail(self, session_id: UUID, max_turns: int) -> list[Turn]:
        return self._tail[-max_turns:]


class FakeConversationRepository:
    def __init__(self) -> None:
        self._records: dict[int, Conversation] = {}
        self._next_id: int = 1
        self._session_ids: set[UUID] = set()

    def save_new(self, conversation: Conversation, session_id: UUID) -> int:
        new_id = self._next_id
        self._next_id += 1
        self._records[new_id] = conversation
        self._session_ids.add(session_id)
        return new_id

    def save_consolidation(self, conversation: Conversation) -> None:
        assert conversation.id is not None
        self._records[conversation.id] = conversation

    def get_unconsolidated(self) -> list[Conversation]:
        return sorted(
            [r for r in self._records.values() if r.is_eligible_for_consolidation],
            key=lambda r: r.started_at,
        )

    def is_session_persisted(self, session_id: UUID) -> bool:
        return session_id in self._session_ids

    def get_last_open_id(self) -> int | None:
        open_ids = [id_ for id_, conv in self._records.items() if not conv.consolidated]
        if not open_ids:
            return None
        return max(open_ids, key=lambda id_: self._records[id_].started_at)

    def extend_conversation(
        self,
        conversation_id: int,
        session_id: UUID,
        turns: list[Turn],
        ended_at: datetime | None,
    ) -> None:
        conv = self._records.get(conversation_id)
        if conv:
            conv.turns.extend(turns)
            conv.ended_at = ended_at
        self._session_ids.add(session_id)


class FakeMemoryRepository:
    def __init__(self) -> None:
        self.episodes: list[Episode] = []
        self.concepts: list[Concept] = []
        self.procedures: list[Procedure] = []
        self.persona_state_writes: list[tuple[MemoryType, int, dict]] = []
        self._next_id: int = 1

    def _next(self) -> int:
        id_ = self._next_id
        self._next_id += 1
        return id_

    def upsert_episode(self, episode: Episode) -> int:
        self.episodes.append(episode)
        return episode.id if episode.id is not None else self._next()

    def upsert_concept(self, concept: Concept) -> int:
        self.concepts.append(concept)
        return concept.id if concept.id is not None else self._next()

    def upsert_procedure(self, procedure: Procedure) -> int:
        self.procedures.append(procedure)
        return procedure.id if procedure.id is not None else self._next()

    def update_persona_state(self, memory_type: MemoryType, item_id: int, persona_state: dict) -> None:
        if memory_type == MemoryType.EPISODE:
            raise ValueError("persona_state does not exist on episode items")
        self.persona_state_writes.append((memory_type, item_id, persona_state))
        items = self.concepts if memory_type == MemoryType.CONCEPT else self.procedures
        for item in items:
            if item.id == item_id:
                item.persona_state = persona_state

    def search(
        self,
        embedding: list[float],
        memory_types: tuple[MemoryType, ...],
        top_n: int,
        persona_id: UUID | None = None,
    ) -> list[MemoryItem]:
        return []


class FakePersonaRepository:
    def __init__(self) -> None:
        self._personas: dict[UUID, AssistantPersona] = {}

    def get(self, persona_id: UUID) -> AssistantPersona | None:
        return self._personas.get(persona_id)

    def list_all(self) -> list[AssistantPersona]:
        return list(self._personas.values())

    def save(self, persona: AssistantPersona) -> None:
        self._personas[persona.id] = persona

    def delete(self, persona_id: UUID) -> None:
        self._personas.pop(persona_id, None)


class FakeUnitOfWork:
    """No-op: fakes have no transactional storage to demarcate, unlike PSUnitOfWork."""

    def __enter__(self) -> "FakeUnitOfWork":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        pass


class FakeMemoryBriefRepository:
    def __init__(self, brief: MemoryBrief | None = None) -> None:
        self._brief = brief

    def get(self) -> MemoryBrief | None:
        return self._brief

    def save(self, brief: MemoryBrief) -> None:
        self._brief = brief


class FakeSessionReplayReader:
    """Sessions provided in chronological order (oldest first)."""

    def __init__(self, sessions: list[tuple[UUID, list[SessionLine]]] | None = None) -> None:
        self._sessions = sessions or []

    def get_unprocessed(
        self,
        is_persisted,
    ) -> list[tuple[UUID, list[SessionLine]]]:
        collected: list[tuple[UUID, list[SessionLine]]] = []
        for session_id, lines in reversed(self._sessions):  # newest-first scan
            if is_persisted(session_id):
                break
            collected.append((session_id, lines))
        return list(reversed(collected))  # oldest-first for processing


class FakeTurnLogger:
    def __init__(self) -> None:
        self.written: list[tuple[UUID, Turn]] = []
        self.closed: dict[UUID, datetime] = {}
        self.clean_exits: dict[UUID, bool] = {}
        self.markers: list[tuple[UUID, ConversationBoundaryType]] = []

    def append(
        self,
        session_id: UUID,
        turn: Turn,
        marker: ConversationBoundaryType | None = None,
        persona_id: UUID | None = None,
    ) -> None:
        self.written.append((session_id, turn))
        if marker is not None:
            self.markers.append((session_id, marker))

    def close(self, session_id: UUID, ended_at: datetime, clean_exit: bool) -> None:
        self.closed[session_id] = ended_at
        self.clean_exits[session_id] = clean_exit


# ---------------------------------------------------------------------------
# Domain protocol fakes
# ---------------------------------------------------------------------------

class FakeRecallIntentDetector:
    def __init__(self, result: RecallTriggered | None = None) -> None:
        self.result = result

    def detect(self, text: str) -> RecallTriggered | None:
        return self.result


class FakeWorthinessEvaluator:
    def __init__(self, worthy: bool = True) -> None:
        self.worthy = worthy

    def evaluate(self, conversation: Conversation) -> bool:
        return self.worthy


class FakeConsolidationExtractor:
    def __init__(self, result: ExtractionResult | None = None) -> None:
        self.result = result or ExtractionResult(episodes=[], concepts=[], procedures=[])
        self.primary_languages: list[Language | None] = []

    def extract(self, conversation: Conversation, primary_language: Language | None = None) -> ExtractionResult:
        self.primary_languages.append(primary_language)
        return self.result


# ---------------------------------------------------------------------------
# Persona extension port fakes
# ---------------------------------------------------------------------------

class FakePersonaSelectionPort:
    def __init__(self, items: list[SelectedItem] | None = None) -> None:
        self.items = items or []
        self.calls: list[tuple[UUID, str | None, EngagementLevel | None, int]] = []

    def select_items(
        self,
        persona_id: UUID,
        category: str | None = None,
        engagement_level: EngagementLevel | None = None,
        limit: int = 10,
    ) -> list[SelectedItem]:
        self.calls.append((persona_id, category, engagement_level, limit))
        return self.items[:limit]


class FakePersonaEnrichmentPort:
    def __init__(self, drafts: list[MemoryItemDraft] | None = None) -> None:
        self.drafts = drafts or []
        self.calls: list[UUID] = []

    def propose_items(self, persona_id: UUID) -> list[MemoryItemDraft]:
        self.calls.append(persona_id)
        return self.drafts


class FakePersonaAssessmentPort:
    def __init__(self, assessments: list[ItemAssessment] | None = None) -> None:
        self.assessments = assessments or []
        self.calls: list[tuple[UUID, Conversation, list[MemoryItem]]] = []

    def assess_items(
        self,
        persona_id: UUID,
        conversation: Conversation,
        touched_items,
    ) -> list[ItemAssessment]:
        self.calls.append((persona_id, conversation, list(touched_items)))
        return self.assessments


class FakeDisambiguationEvaluator:
    def __init__(self, same: bool = False) -> None:
        self.same = same

    def is_same(self, existing: MemoryItem, candidate: MemoryItem) -> bool:
        return self.same


class FakeMemorySynthesizer:
    def synthesize_episode(self, existing_summary: str, new_summary: str) -> str:
        return new_summary

    def synthesize_concept(self, existing: Concept, new_description: str) -> str:
        return new_description

    def synthesize_procedure(
        self, existing: Procedure, new_description: str, new_steps: list[str]
    ) -> tuple[str, list[str]]:
        return new_description, new_steps
