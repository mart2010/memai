from datetime import datetime, UTC
from uuid import uuid4

from memai_server.domain.model import (
    AssistantPersona,
    Concept,
    EngagementLevel,
    Language,
)
from memai_server.infrastructure.language_tutor import (
    LanguageTutorEnrichmentStrategy,
    ProposedItem,
)

from tests.fakes.fakes import FakeMemoryRepository, FakePersonaRepository

PERSONA_ID = uuid4()
NOW = datetime(2026, 7, 12, tzinfo=UTC)

# Two tight clusters in embedding space plus an outlier.
FOOD_A = [1.0, 0.0, 0.0]
FOOD_B = [0.95, 0.05, 0.0]
FOOD_C = [0.9, 0.1, 0.0]
SPORT_A = [0.0, 1.0, 0.0]
SPORT_B = [0.0, 0.95, 0.05]
OUTLIER = [0.0, 0.0, 1.0]


def _persona(settings: dict | None = None) -> AssistantPersona:
    return AssistantPersona(
        id=PERSONA_ID, name="Profesora Sofía", system_prompt="You teach Spanish.",
        languages=[Language("es")], response_language=Language("es"),
        voices={"default": "ff_siwis"}, is_system=False,
        created_at=NOW, updated_at=NOW,
        strategy="language_tutor", settings=settings,
    )


def _seed(
    name: str,
    id_: int,
    embedding: list[float],
    user_initiated: bool = True,
    language: str = "es",
) -> Concept:
    return Concept(
        id=id_, persona_id=PERSONA_ID, name=name, description=f"{name} description",
        language=Language(language), engagement_level=EngagementLevel.EXPLORED,
        persona_state={"user_initiated": user_initiated} if user_initiated else {},
        embedding=embedding,
    )


class FakeClusterProposer:
    def __init__(self, items: list[ProposedItem] | None = None) -> None:
        self.items = items or []
        self.calls: list[tuple[Language, list[Concept], int]] = []

    def propose(self, language, cluster, count):
        self.calls.append((language, list(cluster), count))
        return self.items


def _strategy(
    concepts: list[Concept],
    proposer: FakeClusterProposer | None = None,
    settings: dict | None = None,
) -> tuple[LanguageTutorEnrichmentStrategy, FakeClusterProposer]:
    memory_repo = FakeMemoryRepository()
    memory_repo.concepts.extend(concepts)
    persona_repo = FakePersonaRepository()
    persona_repo.save(_persona(settings))
    proposer = proposer or FakeClusterProposer()
    return LanguageTutorEnrichmentStrategy(
        memory_repo=memory_repo, persona_repo=persona_repo, proposer=proposer,
    ), proposer


class TestInterestClusterDetection:
    def test_too_few_user_initiated_seeds_proposes_nothing(self):
        """Spec: FR-507, TR-807"""
        strategy, proposer = _strategy([
            _seed("paella", 1, FOOD_A),
            _seed("tapas", 2, FOOD_B),
        ])
        assert strategy.propose_items(PERSONA_ID) == []
        assert proposer.calls == []

    def test_scattered_seeds_do_not_form_a_cluster(self):
        """Spec: TR-807"""
        strategy, proposer = _strategy([
            _seed("paella", 1, FOOD_A),
            _seed("fútbol", 2, SPORT_A),
            _seed("cielo", 3, OUTLIER),
        ])
        assert strategy.propose_items(PERSONA_ID) == []
        assert proposer.calls == []

    def test_non_user_initiated_and_unembedded_items_are_not_seeds(self):
        """Spec: TR-807"""
        strategy, proposer = _strategy([
            _seed("paella", 1, FOOD_A, user_initiated=False),
            _seed("tapas", 2, FOOD_B),
            _seed("gazpacho", 3, None),  # no embedding
            _seed("tortilla", 4, FOOD_C),
        ])
        assert strategy.propose_items(PERSONA_ID) == []
        assert proposer.calls == []

    def test_qualifying_cluster_triggers_proposal(self):
        """Spec: FR-507, TR-807"""
        proposer = FakeClusterProposer([
            ProposedItem(name="el mercado", description="Donde se compra comida.", category="noun"),
        ])
        strategy, _ = _strategy(
            [_seed("paella", 1, FOOD_A), _seed("tapas", 2, FOOD_B), _seed("tortilla", 3, FOOD_C)],
            proposer=proposer,
        )
        drafts = strategy.propose_items(PERSONA_ID)

        [(language, cluster, count)] = proposer.calls
        assert language == Language("es")
        assert {c.name for c in cluster} == {"paella", "tapas", "tortilla"}
        assert count == 5  # default enrichment_batch_size

        [draft] = drafts
        assert draft.id is None
        assert draft.persona_id == PERSONA_ID
        assert draft.name == "el mercado"
        assert draft.category == "noun"
        assert draft.language == Language("es")
        assert draft.engagement_level == EngagementLevel.UNSEEN

    def test_largest_qualifying_cluster_wins(self):
        """Spec: TR-807"""
        proposer = FakeClusterProposer([ProposedItem(name="x", description="y")])
        strategy, _ = _strategy(
            [
                _seed("paella", 1, FOOD_A), _seed("tapas", 2, FOOD_B), _seed("tortilla", 3, FOOD_C),
                _seed("fútbol", 4, SPORT_A), _seed("gol", 5, SPORT_B),
            ],
            proposer=proposer,
            settings={"interest_cluster_min_size": 2},
        )
        strategy.propose_items(PERSONA_ID)
        [(_, cluster, _)] = proposer.calls
        assert {c.name for c in cluster} == {"paella", "tapas", "tortilla"}

    def test_cluster_language_is_majority_vote(self):
        """Spec: TR-807"""
        proposer = FakeClusterProposer([ProposedItem(name="x", description="y")])
        strategy, _ = _strategy(
            [
                _seed("paella", 1, FOOD_A, language="es"),
                _seed("tapas", 2, FOOD_B, language="es"),
                _seed("stray", 3, FOOD_C, language="en"),
            ],
            proposer=proposer,
        )
        drafts = strategy.propose_items(PERSONA_ID)
        assert proposer.calls[0][0] == Language("es")
        assert drafts[0].language == Language("es")
