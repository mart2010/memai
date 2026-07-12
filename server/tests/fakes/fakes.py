from datetime import datetime, UTC
from pathlib import Path
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
    BundleInstallRecord,
    ConsolidationExtractor,
    ExtractionResult,
    ItemAssessment,
    MemoryItem,
    MemoryItemDraft,
    Message,
    PersonaBundle,
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
        # Configurable similarity results: search() returns these (filtered by memory
        # type, capped at top_n) regardless of the query embedding.
        self.search_results: list[tuple[float, MemoryItem]] = []
        self.search_calls: list[tuple[list[float], tuple[MemoryType, ...]]] = []
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
    ) -> list[tuple[float, MemoryItem]]:
        self.search_calls.append((embedding, memory_types))

        def _type_of(item: MemoryItem) -> MemoryType:
            if isinstance(item, Episode):
                return MemoryType.EPISODE
            return MemoryType.CONCEPT if isinstance(item, Concept) else MemoryType.PROCEDURE

        return [
            (similarity, item)
            for similarity, item in self.search_results
            if _type_of(item) in memory_types
        ][:top_n]

    def list_items(
        self,
        persona_id: UUID,
        memory_types: tuple[MemoryType, ...],
        category: str | None = None,
        engagement_levels: tuple[EngagementLevel, ...] | None = None,
        limit: int | None = None,
    ) -> list[MemoryItem]:
        if MemoryType.EPISODE in memory_types:
            raise ValueError("list_items covers persona-scoped items only — episodes carry no persona scope")

        def _matches(item) -> bool:
            return (
                item.persona_id == persona_id
                and (category is None or item.category == category)
                and (engagement_levels is None or item.engagement_level in engagement_levels)
            )

        items: list[MemoryItem] = []
        if MemoryType.CONCEPT in memory_types:
            items.extend(sorted((c for c in self.concepts if _matches(c)), key=lambda c: c.id or 0))
        if MemoryType.PROCEDURE in memory_types:
            items.extend(sorted((p for p in self.procedures if _matches(p)), key=lambda p: p.id or 0))
        return items[:limit] if limit is not None else items


class FakePersonaRepository:
    def __init__(self) -> None:
        self._personas: dict[UUID, AssistantPersona] = {}

    def get(self, persona_id: UUID) -> AssistantPersona | None:
        return self._personas.get(persona_id)

    def get_by_key(self, persona_key: str) -> AssistantPersona | None:
        return next((p for p in self._personas.values() if p.persona_key == persona_key), None)

    def list_all(self) -> list[AssistantPersona]:
        return list(self._personas.values())

    def save(self, persona: AssistantPersona) -> None:
        self._personas[persona.id] = persona

    def delete(self, persona_id: UUID) -> None:
        self._personas.pop(persona_id, None)


class FakePersonaBundleSource:
    def __init__(self, bundle: PersonaBundle) -> None:
        self._bundle = bundle
        self.loaded_paths: list[Path] = []

    def load(self, path: Path) -> PersonaBundle:
        self.loaded_paths.append(path)
        return self._bundle


class FakeUnitOfWork:
    """No-op: fakes have no transactional storage to demarcate, unlike PSUnitOfWork.
    Records enter/exit counts so tests can assert transaction granularity (per
    conversation in consolidation, per lesson in bundle install)."""

    def __init__(self) -> None:
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self) -> "FakeUnitOfWork":
        self.enter_count += 1
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.exit_count += 1


class FakeBundleInstallLog:
    def __init__(self) -> None:
        self.records: list[BundleInstallRecord] = []

    def append(self, record: BundleInstallRecord) -> None:
        self.records.append(record)


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
        self.extract_episodes_calls: list[bool] = []

    def extract(
        self,
        conversation: Conversation,
        primary_language: Language | None = None,
        extract_episodes: bool = True,
    ) -> ExtractionResult:
        self.primary_languages.append(primary_language)
        self.extract_episodes_calls.append(extract_episodes)
        return self.result


# ---------------------------------------------------------------------------
# Persona extension port fakes
# ---------------------------------------------------------------------------

class FakePersonaSelectionPort:
    """`focused_items`, when set, is returned for any non-None focus — lets tests assert
    that a [FOCUS: ...] re-fetch actually replaced the default batch."""

    def __init__(
        self,
        items: list[SelectedItem] | None = None,
        focused_items: list[SelectedItem] | None = None,
    ) -> None:
        self.items = items or []
        self.focused_items = focused_items
        self.calls: list[tuple[UUID, str | None, int]] = []

    def select_items(
        self,
        persona_id: UUID,
        focus: str | None = None,
        limit: int = 10,
    ) -> list[SelectedItem]:
        self.calls.append((persona_id, focus, limit))
        if focus is not None and self.focused_items is not None:
            return self.focused_items[:limit]
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
    def __init__(self) -> None:
        self.episode_calls: list[tuple[str, str]] = []
        self.concept_calls: list[tuple[Concept, str]] = []
        self.procedure_calls: list[tuple[Procedure, str, list[str]]] = []

    def synthesize_episode(self, existing_summary: str, new_summary: str) -> str:
        self.episode_calls.append((existing_summary, new_summary))
        return new_summary

    def synthesize_concept(self, existing: Concept, new_description: str) -> str:
        self.concept_calls.append((existing, new_description))
        return new_description

    def synthesize_procedure(
        self, existing: Procedure, new_description: str, new_steps: list[str]
    ) -> tuple[str, list[str]]:
        self.procedure_calls.append((existing, new_description, new_steps))
        return new_description, new_steps
