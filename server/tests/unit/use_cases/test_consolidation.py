import pytest
from datetime import datetime, UTC
from uuid import uuid4

from memai_server.domain.model import (
    AssistantPersona,
    Concept,
    Episode,
    Language,
    Speaker,
    Turn,
    User,
)
from memai_server.use_cases.memory import RunConsolidation
from memai_server.use_cases.ports import ExtractionResult
from memai_server.use_cases.session import EndSession, StartSession

from tests.fakes.fakes import (
    FakeConsolidationExtractor,
    FakeConversationRepository,
    FakeEmbeddingService,
    FakeMemoryBriefRepository,
    FakeMemoryRepository,
    FakePersonaRepository,
    FakeUserRepository,
    FakeWorthinessEvaluator,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _general_assistant() -> AssistantPersona:
    return AssistantPersona.general_assistant("You are helpful.")


def _make_consolidation(
    worthy: bool = True,
    extraction: ExtractionResult | None = None,
) -> tuple[RunConsolidation, FakeConversationRepository, FakeMemoryRepository]:
    conversation_repo = FakeConversationRepository()
    memory_repo = FakeMemoryRepository()
    use_case = RunConsolidation(
        conversation_repo=conversation_repo,
        memory_repo=memory_repo,
        embedding_service=FakeEmbeddingService(),
        extractor=FakeConsolidationExtractor(result=extraction),
        worthiness_evaluator=FakeWorthinessEvaluator(worthy=worthy),
    )
    return use_case, conversation_repo, memory_repo


def _seed_ended_record(conversation_repo: FakeConversationRepository) -> None:
    """Seed one ended, unconsolidated ConversationRecord with a single turn."""
    persona_repo = FakePersonaRepository()
    persona_repo.save(_general_assistant())
    user = User(id=uuid4(), primary_language=Language("en"))

    ctx = StartSession(
        user_repo=FakeUserRepository(user=user),
        persona_repo=persona_repo,
        conversation_repo=conversation_repo,
        memory_brief_repo=FakeMemoryBriefRepository(),
    ).execute(session_id=uuid4(), started_at=_now())

    ctx.conversation_record.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content="hello"))
    EndSession(conversation_repo=conversation_repo).execute(ctx, ended_at=_now())


class TestRunConsolidation:
    @pytest.mark.asyncio
    async def test_worthy_record_produces_episode(self):
        episode = Episode(id=uuid4(), summary="Discussed Python.", happened_at=_now(), conversation_id=uuid4())
        extraction = ExtractionResult(episodes=[episode], concepts=[], procedures=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=True, extraction=extraction)
        _seed_ended_record(conversation_repo)

        count = await use_case.execute()

        assert count == 1
        assert len(memory_repo.episodes) == 1

    @pytest.mark.asyncio
    async def test_unworthy_record_skips_episodes(self):
        episode = Episode(id=uuid4(), summary="Short chat.", happened_at=_now(), conversation_id=uuid4())
        extraction = ExtractionResult(episodes=[episode], concepts=[], procedures=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=False, extraction=extraction)
        _seed_ended_record(conversation_repo)

        await use_case.execute()

        assert len(memory_repo.episodes) == 0

    @pytest.mark.asyncio
    async def test_concepts_extracted_regardless_of_worthiness(self):
        concept = Concept(id=uuid4(), name="Recursion", description="A function calling itself.", language=Language("en"))
        extraction = ExtractionResult(episodes=[], concepts=[concept], procedures=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=False, extraction=extraction)
        _seed_ended_record(conversation_repo)

        await use_case.execute()

        assert len(memory_repo.concepts) == 1

    @pytest.mark.asyncio
    async def test_record_marked_consolidated(self):
        use_case, conversation_repo, _ = _make_consolidation()
        _seed_ended_record(conversation_repo)

        await use_case.execute()

        assert all(r.consolidated for r in conversation_repo._records.values())

    @pytest.mark.asyncio
    async def test_already_consolidated_records_skipped_on_rerun(self):
        use_case, conversation_repo, _ = _make_consolidation()
        _seed_ended_record(conversation_repo)

        await use_case.execute()
        count2 = await use_case.execute()

        assert count2 == 0
