from uuid import uuid4

from memai_server.domain.model import Concept, EngagementLevel, Language, Procedure
from memai_server.services.memory import EnrichMemory

from tests.fakes.fakes import (
    FakeDisambiguationEvaluator,
    FakeEmbeddingService,
    FakeMemoryRepository,
    FakeMemorySynthesizer,
    FakePersonaEnrichmentPort,
    FakeUnitOfWork,
)

PERSONA_ID = uuid4()


def _draft_concept(name: str) -> Concept:
    return Concept(
        id=None, persona_id=PERSONA_ID, name=name, description=f"{name} description",
        language=Language("es"), engagement_level=EngagementLevel.UNSEEN,
    )


def _make_enrich(strategies: dict | None = None) -> tuple[EnrichMemory, FakeMemoryRepository, FakeUnitOfWork]:
    memory_repo = FakeMemoryRepository()
    unit_of_work = FakeUnitOfWork()
    enrich = EnrichMemory(
        memory_repo=memory_repo,
        embedding_service=FakeEmbeddingService(),
        disambiguator=FakeDisambiguationEvaluator(),
        synthesizer=FakeMemorySynthesizer(),
        unit_of_work=unit_of_work,
        enrichment_strategies=strategies,
    )
    return enrich, memory_repo, unit_of_work


class TestEnrichMemory:
    def test_no_strategies_is_a_noop(self):
        enrich, memory_repo, unit_of_work = _make_enrich()
        assert enrich.execute() == 0
        assert memory_repo.concepts == []
        assert unit_of_work.enter_count == 0

    def test_drafts_are_upserted_through_the_shared_pipeline(self):
        strategy = FakePersonaEnrichmentPort(drafts=[_draft_concept("el mercado")])
        enrich, memory_repo, unit_of_work = _make_enrich({PERSONA_ID: strategy})

        assert enrich.execute() == 1
        assert strategy.calls == [PERSONA_ID]
        [concept] = memory_repo.concepts
        assert concept.name == "el mercado"
        assert concept.embedding is not None  # embedded by the upserter
        assert unit_of_work.enter_count == 1  # one transaction per persona batch

    def test_proposals_are_forced_unseen(self):
        draft = _draft_concept("el mercado")
        draft.engagement_level = EngagementLevel.EXPLORED  # a strategy must not claim knowledge
        strategy = FakePersonaEnrichmentPort(drafts=[draft])
        enrich, memory_repo, _ = _make_enrich({PERSONA_ID: strategy})

        enrich.execute()
        assert memory_repo.concepts[0].engagement_level == EngagementLevel.UNSEEN

    def test_procedure_drafts_supported(self):
        draft = Procedure(
            id=None, persona_id=PERSONA_ID, name="pedir la cuenta",
            description="Cómo pedir la cuenta.", language=Language("es"),
            engagement_level=EngagementLevel.UNSEEN,
        )
        strategy = FakePersonaEnrichmentPort(drafts=[draft])
        enrich, memory_repo, _ = _make_enrich({PERSONA_ID: strategy})

        assert enrich.execute() == 1
        assert memory_repo.procedures[0].name == "pedir la cuenta"

    def test_empty_proposals_skip_the_transaction(self):
        strategy = FakePersonaEnrichmentPort(drafts=[])
        enrich, _, unit_of_work = _make_enrich({PERSONA_ID: strategy})

        assert enrich.execute() == 0
        assert unit_of_work.enter_count == 0
