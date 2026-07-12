from datetime import datetime, timedelta, UTC
from uuid import uuid4

from memai_server.domain.model import (
    AssistantPersona,
    Concept,
    EngagementLevel,
    Episode,
    Language,
    Procedure,
)
from memai_server.infrastructure.language_tutor import (
    LanguageTutorSelectionStrategy,
    TutorFocus,
)

from tests.fakes.fakes import (
    FakeEmbeddingService,
    FakeMemoryRepository,
    FakePersonaRepository,
)

PERSONA_ID = uuid4()
NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _persona(settings: dict | None = None) -> AssistantPersona:
    return AssistantPersona(
        id=PERSONA_ID, name="Profesora Sofía", system_prompt="You teach Spanish.",
        languages=[Language("es")], response_language=Language("es"),
        voices={"default": "ff_siwis"}, is_system=False,
        created_at=NOW, updated_at=NOW,
        strategy="language_tutor", settings=settings,
    )


def _concept(
    name: str,
    id_: int,
    level: EngagementLevel = EngagementLevel.UNSEEN,
    category: str | None = None,
    created_minutes: int = 0,
    updated_minutes: int = 0,
    persona_state: dict | None = None,
    embedding: list[float] | None = None,
) -> Concept:
    return Concept(
        id=id_, persona_id=PERSONA_ID, name=name, description=f"{name} description",
        language=Language("es"), category=category, persona_state=persona_state,
        engagement_level=level,
        created_at=NOW + timedelta(minutes=created_minutes),
        updated_at=NOW + timedelta(minutes=updated_minutes),
        embedding=embedding,
    )


def _procedure(name: str, id_: int, created_minutes: int = 0) -> Procedure:
    return Procedure(
        id=id_, persona_id=PERSONA_ID, name=name, description=f"{name} description",
        language=Language("es"), category="construction",
        engagement_level=EngagementLevel.UNSEEN,
        created_at=NOW + timedelta(minutes=created_minutes),
        updated_at=NOW + timedelta(minutes=created_minutes),
    )


class FakeFocusInterpreter:
    def __init__(self, result: TutorFocus | None = None) -> None:
        self.result = result or TutorFocus()
        self.calls: list[tuple[str, list[str]]] = []

    def interpret(self, focus: str, categories) -> TutorFocus:
        self.calls.append((focus, list(categories)))
        return self.result


def _strategy(
    concepts: list[Concept] | None = None,
    procedures: list[Procedure] | None = None,
    settings: dict | None = None,
    interpreter: FakeFocusInterpreter | None = None,
    memory_repo: FakeMemoryRepository | None = None,
    embedding_vector: list[float] | None = None,
) -> tuple[LanguageTutorSelectionStrategy, FakeMemoryRepository, FakeFocusInterpreter]:
    memory_repo = memory_repo or FakeMemoryRepository()
    memory_repo.concepts.extend(concepts or [])
    memory_repo.procedures.extend(procedures or [])
    persona_repo = FakePersonaRepository()
    persona_repo.save(_persona(settings))
    interpreter = interpreter or FakeFocusInterpreter()
    strategy = LanguageTutorSelectionStrategy(
        memory_repo=memory_repo,
        persona_repo=persona_repo,
        embedding_service=FakeEmbeddingService(vector=embedding_vector),
        focus_interpreter=interpreter,
        now_fn=lambda: NOW,
    )
    return strategy, memory_repo, interpreter


def _names(selected) -> list[str]:
    return [s.item.name for s in selected]


class TestDefaultComposition:
    def test_empty_memory_returns_empty_batch(self):
        """Spec: TR-803"""
        strategy, _, _ = _strategy()
        assert strategy.select_items(PERSONA_ID) == []

    def test_mixed_batch_reviews_ranked_then_new_in_curriculum_order(self):
        """Spec: FR-501, TR-802, TR-803"""
        concepts = [
            _concept("nuevo1", 1, EngagementLevel.UNSEEN, created_minutes=1),
            _concept("nuevo2", 2, EngagementLevel.UNSEEN, created_minutes=2),
            _concept("integrado", 3, EngagementLevel.INTEGRATED),
            _concept("mencionado", 4, EngagementLevel.MENTIONED),
            _concept("explorado", 5, EngagementLevel.EXPLORED),
        ]
        strategy, _, _ = _strategy(concepts)
        selected = strategy.select_items(PERSONA_ID, limit=4)

        # 2 reviews (least-known first: mentioned, explored) + 2 new (curriculum order).
        assert set(_names(selected)) == {"mencionado", "explorado", "nuevo1", "nuevo2"}

    def test_review_ranking_least_known_then_stalest_first(self):
        """Spec: TR-802, FR-506"""
        concepts = [
            _concept("fresh_mentioned", 1, EngagementLevel.MENTIONED, updated_minutes=60),
            _concept("stale_mentioned", 2, EngagementLevel.MENTIONED, updated_minutes=0),
            _concept("explored", 3, EngagementLevel.EXPLORED),
        ]
        strategy, _, _ = _strategy(concepts)
        selected = strategy.select_items(PERSONA_ID, limit=3)
        assert _names(selected) == ["stale_mentioned", "fresh_mentioned", "explored"]

    def test_new_items_follow_cross_type_curriculum_order(self):
        """Spec: TR-802, INV-11"""
        # Concepts and procedures have independent id sequences — created_at carries
        # curriculum order across types (sequential install), id breaks ties.
        concepts = [_concept("c_late", 1, created_minutes=30), _concept("c_early", 2, created_minutes=1)]
        procedures = [_procedure("p_mid", 1, created_minutes=10)]
        strategy, _, _ = _strategy(concepts, procedures)
        selected = strategy.select_items(PERSONA_ID, limit=3)
        assert _names(selected) == ["c_early", "p_mid", "c_late"]

    def test_short_review_pool_backfills_with_new_items(self):
        """Spec: TR-803"""
        concepts = [
            _concept("review1", 1, EngagementLevel.MENTIONED),
            _concept("n1", 2, created_minutes=1),
            _concept("n2", 3, created_minutes=2),
            _concept("n3", 4, created_minutes=3),
        ]
        strategy, _, _ = _strategy(concepts)
        selected = strategy.select_items(PERSONA_ID, limit=4)
        assert len(selected) == 4  # 1 review + 3 new, nothing wasted

    def test_batch_interleaved_by_category(self):
        """Spec: FR-501, TR-803"""
        concepts = [
            _concept("n1", 1, category="noun", created_minutes=1),
            _concept("n2", 2, category="noun", created_minutes=2),
            _concept("v1", 3, category="verb", created_minutes=3),
            _concept("v2", 4, category="verb", created_minutes=4),
        ]
        strategy, _, _ = _strategy(concepts)
        selected = strategy.select_items(PERSONA_ID, limit=4)
        categories = [s.item.category for s in selected]
        assert categories == ["noun", "verb", "noun", "verb"]  # round-robin, order kept


class TestFocusSteering:
    def test_no_focus_never_calls_interpreter(self):
        """Spec: TR-804"""
        strategy, _, interpreter = _strategy([_concept("hola", 1)])
        strategy.select_items(PERSONA_ID)
        assert interpreter.calls == []

    def test_interpreter_receives_verbatim_focus_and_present_categories(self):
        """Spec: FR-502, TR-804"""
        concepts = [_concept("hola", 1, category="noun"), _concept("ser", 2, category="verb")]
        strategy, _, interpreter = _strategy(concepts)
        strategy.select_items(PERSONA_ID, focus="just review old vocabulary today")
        assert interpreter.calls == [("just review old vocabulary today", ["noun", "verb"])]

    def test_review_mode_excludes_new_items(self):
        """Spec: FR-502, TR-803"""
        concepts = [
            _concept("nuevo", 1, EngagementLevel.UNSEEN),
            _concept("conocido", 2, EngagementLevel.MENTIONED),
        ]
        interpreter = FakeFocusInterpreter(TutorFocus(mode="review"))
        strategy, _, _ = _strategy(concepts, interpreter=interpreter)
        selected = strategy.select_items(PERSONA_ID, focus="review only")
        assert _names(selected) == ["conocido"]

    def test_new_mode_excludes_review_items(self):
        """Spec: FR-502, TR-803"""
        concepts = [
            _concept("nuevo", 1, EngagementLevel.UNSEEN),
            _concept("conocido", 2, EngagementLevel.MENTIONED),
        ]
        interpreter = FakeFocusInterpreter(TutorFocus(mode="new"))
        strategy, _, _ = _strategy(concepts, interpreter=interpreter)
        selected = strategy.select_items(PERSONA_ID, focus="teach me a new word")
        assert _names(selected) == ["nuevo"]

    def test_category_focus_filters_items(self):
        """Spec: TR-803"""
        concepts = [
            _concept("hola", 1, category="noun"),
            _concept("ser", 2, category="verb"),
        ]
        interpreter = FakeFocusInterpreter(TutorFocus(category="verb"))
        strategy, _, _ = _strategy(concepts, interpreter=interpreter)
        selected = strategy.select_items(PERSONA_ID, focus="a verb please")
        assert _names(selected) == ["ser"]

    def test_unmatched_category_does_not_zero_the_session(self):
        """Spec: TR-803"""
        concepts = [_concept("hola", 1, category="noun")]
        interpreter = FakeFocusInterpreter(TutorFocus(category="idiom"))
        strategy, _, _ = _strategy(concepts, interpreter=interpreter)
        selected = strategy.select_items(PERSONA_ID, focus="idioms")
        assert _names(selected) == ["hola"]

    def test_topic_focus_ranks_by_similarity_to_topic(self):
        """Spec: FR-502, TR-803"""
        concepts = [
            _concept("lejano", 1, embedding=[0.0, 1.0], created_minutes=1),
            _concept("cercano", 2, embedding=[1.0, 0.0], created_minutes=2),
        ]
        interpreter = FakeFocusInterpreter(TutorFocus(topic="food"))
        strategy, _, _ = _strategy(
            concepts, interpreter=interpreter, embedding_vector=[1.0, 0.0],
        )
        selected = strategy.select_items(PERSONA_ID, focus="food words")
        assert _names(selected) == ["cercano", "lejano"]


class TestRetentionRanking:
    def test_retention_mode_ranks_lowest_retention_first(self):
        """Spec: FR-506, TR-802, TR-808"""
        state_due = {"last_practiced_at": "2026-07-01", "half_life_days": 2.0}      # long ago, short half-life
        state_fresh = {"last_practiced_at": "2026-07-11", "half_life_days": 30.0}   # yesterday, long half-life
        concepts = [
            _concept("fresco", 1, EngagementLevel.EXPLORED, persona_state=state_fresh),
            _concept("olvidado", 2, EngagementLevel.EXPLORED, persona_state=state_due),
            _concept("sin_estado", 3, EngagementLevel.EXPLORED),  # never practised → most due
        ]
        strategy, _, _ = _strategy(concepts, settings={"ranking": "retention"})
        selected = strategy.select_items(PERSONA_ID, limit=3)
        assert _names(selected) == ["sin_estado", "olvidado", "fresco"]

    def test_engagement_ranking_is_the_default_even_with_state_present(self):
        """Spec: FR-506, TR-802"""
        state = {"last_practiced_at": "2026-07-01", "half_life_days": 2.0}
        concepts = [
            _concept("explorado", 1, EngagementLevel.EXPLORED, persona_state=state),
            _concept("mencionado", 2, EngagementLevel.MENTIONED),
        ]
        strategy, _, _ = _strategy(concepts)  # no ranking setting
        selected = strategy.select_items(PERSONA_ID, limit=2)
        assert _names(selected) == ["mencionado", "explorado"]


class TestEpisodePairing:
    def test_similar_episode_becomes_anchor_context(self):
        """Spec: FR-503, TR-805"""
        episode = Episode(id=1, summary="A memorable dinner in Madrid.",
                          happened_at=NOW, origin_conversation_id=1)
        memory_repo = FakeMemoryRepository()
        memory_repo.search_results = [(0.8, episode)]
        concepts = [_concept("la comida", 1, embedding=[1.0, 0.0])]
        strategy, _, _ = _strategy(concepts, memory_repo=memory_repo)

        selected = strategy.select_items(PERSONA_ID)
        assert "A memorable dinner in Madrid." in selected[0].context

    def test_low_similarity_yields_elicitation_hint(self):
        """Spec: FR-503, TR-805"""
        episode = Episode(id=1, summary="Unrelated.", happened_at=NOW, origin_conversation_id=1)
        memory_repo = FakeMemoryRepository()
        memory_repo.search_results = [(0.3, episode)]
        concepts = [_concept("la comida", 1, embedding=[1.0, 0.0])]
        strategy, _, _ = _strategy(concepts, memory_repo=memory_repo)

        selected = strategy.select_items(PERSONA_ID)
        assert "invite the user to share" in selected[0].context
        assert "la comida" in selected[0].context

    def test_elicitation_hints_capped_per_batch(self):
        """Spec: FR-503, TR-805"""
        concepts = [
            _concept(f"item{i}", i, embedding=[1.0, 0.0], created_minutes=i) for i in range(1, 5)
        ]
        strategy, _, _ = _strategy(concepts)  # no episodes at all → every item misses

        selected = strategy.select_items(PERSONA_ID)
        hints = [s for s in selected if s.context is not None]
        assert len(hints) == 2  # default elicitation_cap

    def test_elicitation_cap_read_from_persona_settings(self):
        """Spec: TR-801, TR-805"""
        concepts = [
            _concept(f"item{i}", i, embedding=[1.0, 0.0], created_minutes=i) for i in range(1, 4)
        ]
        strategy, _, _ = _strategy(concepts, settings={"elicitation_cap": 0})

        selected = strategy.select_items(PERSONA_ID)
        assert all(s.context is None for s in selected)

    def test_items_without_embedding_are_not_paired(self):
        """Spec: TR-805"""
        concepts = [_concept("sin_embedding", 1)]
        strategy, memory_repo, _ = _strategy(concepts)

        selected = strategy.select_items(PERSONA_ID)
        assert selected[0].context is None
        assert memory_repo.search_calls == []
