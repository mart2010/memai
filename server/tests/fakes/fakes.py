from datetime import datetime, UTC
from uuid import UUID

from memai_server.domain.events import RecallTriggered
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
from memai_server.domain.protocols import WorthinessEvaluator
from memai_server.services.ports import (
    ConsolidationExtractor,
    ExtractionResult,
    MemoryItem,
    Message,
    SessionInfo,
)


# ---------------------------------------------------------------------------
# Infrastructure fakes
# ---------------------------------------------------------------------------

class FakeSTTService:
    def __init__(self, transcript: str = "hello", language: Language = Language("en")) -> None:
        self.transcript = transcript
        self.language = language
        self.calls: list[tuple[bytes, Language]] = []

    def transcribe(self, audio: bytes, language_hint: Language) -> tuple[str, Language]:
        self.calls.append((audio, language_hint))
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
        self.synthesised: list[str] = []

    def synthesise(self, text: str) -> bytes:
        self.synthesised.append(text)
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

    def save_new(self, conversation: Conversation, session_id: UUID) -> int:
        new_id = self._next_id
        self._next_id += 1
        self._records[new_id] = conversation
        return new_id

    def save_consolidation(self, conversation: Conversation) -> None:
        assert conversation.id is not None
        self._records[conversation.id] = conversation

    def get_unconsolidated(self) -> list[Conversation]:
        return sorted(
            [r for r in self._records.values() if r.is_eligible_for_consolidation],
            key=lambda r: r.started_at,
        )


class FakeMemoryRepository:
    def __init__(self) -> None:
        self.episodes: list[Episode] = []
        self.concepts: list[Concept] = []
        self.procedures: list[Procedure] = []
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


class FakeMemoryBriefRepository:
    def __init__(self, brief: MemoryBrief | None = None) -> None:
        self._brief = brief

    def get(self) -> MemoryBrief | None:
        return self._brief

    def save(self, brief: MemoryBrief) -> None:
        self._brief = brief


class FakeTurnLogger:
    def __init__(self) -> None:
        self.written: list[tuple[UUID, Turn]] = []
        self.closed: dict[UUID, datetime] = {}
        self.clean_exits: dict[UUID, bool] = {}
        self.markers: list[tuple[UUID, str]] = []

    def append(self, session_id: UUID, turn: Turn, marker: str | None = None) -> None:
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

    def extract(self, conversation: Conversation) -> ExtractionResult:
        return self.result
