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
        """Spec: TR-705"""
        enrich, memory_repo, unit_of_work = _make_enrich()
        assert enrich.execute() == 0
        assert memory_repo.concepts == []
        assert unit_of_work.enter_count == 0

    def test_drafts_are_upserted_through_the_shared_pipeline(self):
        """Spec: TR-705, FR-507"""
        strategy = FakePersonaEnrichmentPort(drafts=[_draft_concept("el mercado")])
        enrich, memory_repo, unit_of_work = _make_enrich({PERSONA_ID: strategy})

        assert enrich.execute() == 1
        assert strategy.calls == [PERSONA_ID]
        [concept] = memory_repo.concepts
        assert concept.name == "el mercado"
        assert concept.embedding is not None  # embedded by the upserter
        assert unit_of_work.enter_count == 1  # one transaction per persona batch

    def test_proposals_are_forced_unseen(self):
        """Spec: INV-12, TR-705"""
        draft = _draft_concept("el mercado")
        draft.engagement_level = EngagementLevel.EXPLORED  # a strategy must not claim knowledge
        strategy = FakePersonaEnrichmentPort(drafts=[draft])
        enrich, memory_repo, _ = _make_enrich({PERSONA_ID: strategy})

        enrich.execute()
        assert memory_repo.concepts[0].engagement_level == EngagementLevel.UNSEEN

    def test_procedure_drafts_supported(self):
        """Spec: TR-705"""
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
        """Spec: TR-705"""
        strategy = FakePersonaEnrichmentPort(drafts=[])
        enrich, _, unit_of_work = _make_enrich({PERSONA_ID: strategy})

        assert enrich.execute() == 0
        assert unit_of_work.enter_count == 0

    def test_concept_draft_is_marked_authored(self):
        """Spec: FR-407 — curriculum drafts are curated content, protected from later
        live-extraction rewrites via Concept.origin, not via which persona proposed them."""
        strategy = FakePersonaEnrichmentPort(drafts=[_draft_concept("el mercado")])
        enrich, memory_repo, _ = _make_enrich({PERSONA_ID: strategy})

        enrich.execute()

        assert memory_repo.concepts[0].origin == "authored"

    def test_matching_existing_authored_concept_is_touched_not_rewritten(self):
        """Spec: FR-407 — a proposal landing on existing curated content is a touch, never a rewrite."""
        existing = Concept(
            id=1, persona_id=PERSONA_ID, name="el mercado", description="Curated definition.",
            language=Language("es"), origin="authored", engagement_level=EngagementLevel.MENTIONED,
        )
        memory_repo = FakeMemoryRepository()
        memory_repo.search_results = [(0.95, existing)]  # authored-protection band
        draft = _draft_concept("el mercado")
        draft.description = "A differently-worded proposal."
        strategy = FakePersonaEnrichmentPort(drafts=[draft])
        enrich = EnrichMemory(
            memory_repo=memory_repo,
            embedding_service=FakeEmbeddingService(),
            disambiguator=FakeDisambiguationEvaluator(),
            synthesizer=FakeMemorySynthesizer(),
            unit_of_work=FakeUnitOfWork(),
            enrichment_strategies={PERSONA_ID: strategy},
        )

        enrich.execute()

        assert memory_repo.concepts[0].description == "Curated definition."

    def test_matching_existing_procedure_keeps_curated_content(self):
        """Spec: FR-407"""
        existing = Procedure(
            id=1, persona_id=PERSONA_ID, name="pedir la cuenta",
            description="Curated steps.", language=Language("es"),
        )
        memory_repo = FakeMemoryRepository()
        memory_repo.search_results = [(0.95, existing)]
        draft = Procedure(
            id=None, persona_id=PERSONA_ID, name="pedir la cuenta",
            description="A rewritten version.", language=Language("es"),
            engagement_level=EngagementLevel.UNSEEN,
        )
        strategy = FakePersonaEnrichmentPort(drafts=[draft])
        enrich = EnrichMemory(
            memory_repo=memory_repo,
            embedding_service=FakeEmbeddingService(),
            disambiguator=FakeDisambiguationEvaluator(),
            synthesizer=FakeMemorySynthesizer(),
            unit_of_work=FakeUnitOfWork(),
            enrichment_strategies={PERSONA_ID: strategy},
        )

        enrich.execute()

        assert memory_repo.procedures[0].description == "Curated steps."
