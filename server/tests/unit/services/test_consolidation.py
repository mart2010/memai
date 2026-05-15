import pytest
from datetime import datetime, UTC

from memai_server.domain.model import (
    AssistantPersona,
    Concept,
    Conversation,
    Episode,
    Language,
    Speaker,
    Turn,
)
from memai_server.services.memory import RunConsolidation
from memai_server.services.ports import ExtractionResult

from tests.fakes.fakes import (
    FakeConsolidationExtractor,
    FakeConversationRepository,
    FakeEmbeddingService,
    FakeMemoryRepository,
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


def _seed_ended_conversation(conversation_repo: FakeConversationRepository) -> None:
    conv = Conversation(
        id=None,
        started_at=_now(),
        persona_snapshot=_general_assistant(),
    )
    conv.add_turn(Turn(timestamp=_now(), speaker=Speaker.USER, content="hello"))
    conv.end(ended_at=_now())
    conversation_repo.save(conv)


class TestRunConsolidation:
    @pytest.mark.asyncio
    async def test_worthy_conversation_produces_episode(self):
        episode = Episode(id=None, summary="Discussed Python.", happened_at=_now(), conversation_id=1)
        extraction = ExtractionResult(episodes=[episode], concepts=[], procedures=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=True, extraction=extraction)
        _seed_ended_conversation(conversation_repo)

        count = await use_case.execute()

        assert count == 1
        assert len(memory_repo.episodes) == 1

    @pytest.mark.asyncio
    async def test_unworthy_conversation_skips_episodes(self):
        episode = Episode(id=None, summary="Short chat.", happened_at=_now(), conversation_id=1)
        extraction = ExtractionResult(episodes=[episode], concepts=[], procedures=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=False, extraction=extraction)
        _seed_ended_conversation(conversation_repo)

        await use_case.execute()

        assert len(memory_repo.episodes) == 0

    @pytest.mark.asyncio
    async def test_concepts_extracted_regardless_of_worthiness(self):
        concept = Concept(id=None, name="Recursion", description="A function calling itself.", language=Language("en"))
        extraction = ExtractionResult(episodes=[], concepts=[concept], procedures=[])
        use_case, conversation_repo, memory_repo = _make_consolidation(worthy=False, extraction=extraction)
        _seed_ended_conversation(conversation_repo)

        await use_case.execute()

        assert len(memory_repo.concepts) == 1

    @pytest.mark.asyncio
    async def test_conversation_marked_consolidated(self):
        use_case, conversation_repo, _ = _make_consolidation()
        _seed_ended_conversation(conversation_repo)

        await use_case.execute()

        assert all(r.consolidated for r in conversation_repo._records.values())

    @pytest.mark.asyncio
    async def test_already_consolidated_conversations_skipped_on_rerun(self):
        use_case, conversation_repo, _ = _make_consolidation()
        _seed_ended_conversation(conversation_repo)

        await use_case.execute()
        count2 = await use_case.execute()

        assert count2 == 0
