"""MemoryUpserter — merge/insert returns and the exact-duplicate short-circuit.

The threshold/merge semantics themselves are covered end-to-end through the
ConsolidateMemory tests; here we pin the upserter's own contract: the merged-flag
return (feeds the installer's provenance counts) and the short-circuit that makes
bundle reinstalls near-free.
"""
from uuid import uuid4

from memai_server.domain.model import Concept, EngagementLevel, Language, Procedure
from memai_server.services.upsert import MemoryUpserter

from tests.fakes.fakes import (
    FakeDisambiguationEvaluator,
    FakeEmbeddingService,
    FakeMemoryRepository,
    FakeMemorySynthesizer,
)

PERSONA_ID = uuid4()


class _CannedSearchMemoryRepository(FakeMemoryRepository):
    """Returns a fixed similarity-search result so merge paths can be exercised."""

    def __init__(self, results) -> None:
        super().__init__()
        self._results = results

    def search(self, embedding, memory_types, top_n, persona_id=None):
        return self._results


def _concept(**overrides) -> Concept:
    defaults = dict(
        id=None, persona_id=PERSONA_ID, name="hola",
        description="The standard Spanish greeting.", language=Language("es"),
    )
    defaults.update(overrides)
    return Concept(**defaults)


def _procedure(**overrides) -> Procedure:
    defaults = dict(
        id=None, persona_id=PERSONA_ID, name="greeting politely",
        description="How to greet politely.", language=Language("es"),
        steps=["hola", "¿cómo está?"],
    )
    defaults.update(overrides)
    return Procedure(**defaults)


def _upserter(memory_repo=None, synthesizer=None):
    return MemoryUpserter(
        memory_repo if memory_repo is not None else FakeMemoryRepository(),
        FakeEmbeddingService(),
        FakeDisambiguationEvaluator(),
        synthesizer if synthesizer is not None else FakeMemorySynthesizer(),
    )


class TestMergedFlagReturn:
    def test_insert_returns_false_and_assigns_id(self):
        concept = _concept()
        merged = _upserter().upsert_concept(concept, PERSONA_ID)
        assert merged is False
        assert concept.id is not None

    def test_merge_returns_true_and_reuses_id(self):
        existing = _concept(id=42, description="An older synthesis.")
        repo = _CannedSearchMemoryRepository([(0.95, existing)])  # auto-merge band
        concept = _concept()
        merged = _upserter(memory_repo=repo).upsert_concept(concept, PERSONA_ID)
        assert merged is True
        assert concept.id == 42


class TestExactDuplicateShortCircuit:
    def test_identical_concept_skips_synthesis(self):
        """Reinstalling a bundle re-upserts byte-identical items — no LLM call."""
        existing = _concept(id=42, engagement_level=EngagementLevel.EXPLORED)
        repo = _CannedSearchMemoryRepository([(1.0, existing)])
        synthesizer = FakeMemorySynthesizer()
        concept = _concept(engagement_level=EngagementLevel.UNSEEN)

        merged = _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_concept(concept, PERSONA_ID)

        assert merged is True
        assert synthesizer.concept_calls == []
        assert concept.id == 42
        # Max-engagement rule still applies: the duplicate cannot downgrade knowledge.
        assert concept.engagement_level == EngagementLevel.EXPLORED

    def test_differing_concept_description_still_synthesizes(self):
        existing = _concept(id=42, description="An older synthesis.")
        repo = _CannedSearchMemoryRepository([(0.95, existing)])
        synthesizer = FakeMemorySynthesizer()

        _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_concept(_concept(), PERSONA_ID)

        assert len(synthesizer.concept_calls) == 1

    def test_identical_procedure_skips_synthesis(self):
        existing = _procedure(id=7)
        repo = _CannedSearchMemoryRepository([(1.0, existing)])
        synthesizer = FakeMemorySynthesizer()

        merged = _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_procedure(_procedure(), PERSONA_ID)

        assert merged is True
        assert synthesizer.procedure_calls == []

    def test_procedure_with_differing_steps_still_synthesizes(self):
        """Same name + description but new steps is new evidence, not a duplicate."""
        existing = _procedure(id=7)
        repo = _CannedSearchMemoryRepository([(1.0, existing)])
        synthesizer = FakeMemorySynthesizer()

        _upserter(memory_repo=repo, synthesizer=synthesizer).upsert_procedure(
            _procedure(steps=["hola", "¿cómo está?", "mucho gusto"]), PERSONA_ID
        )

        assert len(synthesizer.procedure_calls) == 1
