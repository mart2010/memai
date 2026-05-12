from datetime import datetime, UTC
from uuid import UUID

from memai_server.domain.events import RecallTriggered
from memai_server.domain.model import (
    AssistantPersona,
    ConversationRecord,
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
from memai_server.use_cases.ports import (
    ConsolidationExtractor,
    ExtractionResult,
    MemoryItem,
    Message,
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


class FakeConversationRepository:
    def __init__(self) -> None:
        self._records: dict[UUID, ConversationRecord] = {}

    def save(self, record: ConversationRecord) -> None:
        self._records[record.id] = record

    def get_unconsolidated(self) -> list[ConversationRecord]:
        return sorted(
            [r for r in self._records.values() if r.is_eligible_for_consolidation],
            key=lambda r: r.started_at,
        )


class FakeMemoryRepository:
    def __init__(self) -> None:
        self.episodes: list[Episode] = []
        self.concepts: list[Concept] = []
        self.procedures: list[Procedure] = []

    def upsert_episode(self, episode: Episode) -> None:
        self.episodes.append(episode)

    def upsert_concept(self, concept: Concept) -> None:
        self.concepts.append(concept)

    def upsert_procedure(self, procedure: Procedure) -> None:
        self.procedures.append(procedure)

    def search(
        self,
        embedding: list[float],
        memory_types: tuple[MemoryType, ...],
        top_n: int,
    ) -> list[MemoryItem]:
        return []


class FakePersonaRepository:
    def __init__(self) -> None:
        self._personas: dict[UUID, AssistantPersona] = {}
        self._language_map: dict[str, UUID] = {}

    def get(self, persona_id: UUID) -> AssistantPersona | None:
        return self._personas.get(persona_id)

    def list_all(self) -> list[AssistantPersona]:
        return list(self._personas.values())

    def find_by_language(self, language: Language) -> AssistantPersona | None:
        pid = self._language_map.get(language.code)
        return self._personas.get(pid) if pid else None

    def save(self, persona: AssistantPersona) -> None:
        self._personas[persona.id] = persona

    def delete(self, persona_id: UUID) -> None:
        self._personas.pop(persona_id, None)

    def register_language(self, language: Language, persona_id: UUID) -> None:
        """Test helper: associate a language code with a persona for find_by_language."""
        self._language_map[language.code] = persona_id


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

    def append(self, session_id: UUID, turn: Turn) -> None:
        self.written.append((session_id, turn))

    def close(self, session_id: UUID, ended_at: datetime) -> None:
        self.closed[session_id] = ended_at


# ---------------------------------------------------------------------------
# Domain protocol fakes
# ---------------------------------------------------------------------------

class FakeRecallIntentDetector:
    def __init__(self, result: RecallTriggered | None = None) -> None:
        self.result = result

    def detect(self, text: str) -> RecallTriggered | None:
        return self.result


class FakePersonaIntentDetector:
    def __init__(self, result: str | None = None) -> None:
        self.result = result

    def detect(self, text: str) -> str | None:
        return self.result


class FakeWorthinessEvaluator:
    def __init__(self, worthy: bool = True) -> None:
        self.worthy = worthy

    def evaluate(self, record: ConversationRecord) -> bool:
        return self.worthy


class FakeConsolidationExtractor:
    def __init__(self, result: ExtractionResult | None = None) -> None:
        self.result = result or ExtractionResult(episodes=[], concepts=[], procedures=[])

    def extract(self, record: ConversationRecord) -> ExtractionResult:
        return self.result
