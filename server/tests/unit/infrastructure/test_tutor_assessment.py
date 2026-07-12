from datetime import datetime, timedelta, UTC
from uuid import uuid4

from memai_server.domain.model import (
    Concept,
    Conversation,
    EngagementLevel,
    Episode,
    Language,
    AssistantPersona,
    MemoryType,
    Speaker,
    Turn,
    User,
)
from memai_server.infrastructure.language_tutor import (
    LanguageTutorAssessmentStrategy,
    PracticeJudgment,
)

from tests.fakes.fakes import (
    FakeMemoryRepository,
    FakePersonaRepository,
    FakeUserRepository,
)

PERSONA_ID = uuid4()
T0 = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)


def _persona(settings: dict | None = None) -> AssistantPersona:
    return AssistantPersona(
        id=PERSONA_ID, name="Profesora Sofía", system_prompt="You teach Spanish.",
        languages=[Language("es")], response_language=Language("es"),
        voices={"default": "ff_siwis"}, is_system=False,
        created_at=T0, updated_at=T0,
        strategy="language_tutor", settings=settings,
    )


def _conversation(turns: list[Turn] | None = None) -> Conversation:
    conversation = Conversation(id=1, started_at=T0, persona_id=PERSONA_ID)
    conversation.turns = turns if turns is not None else [
        Turn(timestamp=T0, speaker=Speaker.ASSISTANT, content="¿Cómo se dice 'food'?"),
        Turn(timestamp=T0 + timedelta(seconds=3), speaker=Speaker.USER, content="La comida."),
    ]
    conversation.ended_at = T0 + timedelta(minutes=10)
    return conversation


def _touched_concept(name: str, id_: int) -> Concept:
    # Extraction output post-upsert: id set, persona_state ALWAYS None (upserts
    # structurally exclude the column) — stored state lives only in the repo.
    return Concept(
        id=id_, persona_id=PERSONA_ID, name=name, description=f"{name} description",
        language=Language("es"), engagement_level=EngagementLevel.MENTIONED,
    )


def _stored_concept(name: str, id_: int, persona_state: dict | None) -> Concept:
    concept = _touched_concept(name, id_)
    concept.persona_state = persona_state
    return concept


class FakePracticeJudge:
    def __init__(self, judgments: list[PracticeJudgment] | None = None) -> None:
        self.judgments = judgments or []
        self.calls: list[tuple[Conversation, list]] = []

    def judge(self, conversation, items):
        self.calls.append((conversation, list(items)))
        return self.judgments


def _strategy(
    judgments: list[PracticeJudgment] | None = None,
    stored: list[Concept] | None = None,
    settings: dict | None = None,
    primary_language: Language | None = Language("fr"),
) -> LanguageTutorAssessmentStrategy:
    memory_repo = FakeMemoryRepository()
    memory_repo.concepts.extend(stored or [])
    persona_repo = FakePersonaRepository()
    persona_repo.save(_persona(settings))
    return LanguageTutorAssessmentStrategy(
        memory_repo=memory_repo,
        persona_repo=persona_repo,
        user_repo=FakeUserRepository(User(id=uuid4(), primary_language=primary_language)),
        judge=FakePracticeJudge(judgments),
    )


class TestInitialState:
    def test_first_assessment_creates_full_state(self):
        strategy = _strategy(
            judgments=[PracticeJudgment(name="la comida", retrievals=2, errors=0, user_initiated=True)],
        )
        [assessment] = strategy.assess_items(
            PERSONA_ID, _conversation(), [_touched_concept("la comida", 1)]
        )

        assert assessment.item_id == 1
        assert assessment.memory_type is MemoryType.CONCEPT
        state = assessment.persona_state
        assert state["last_practiced_at"] == "2026-07-12"
        assert state["retrievals"] == 2
        assert state["errors"] == 0
        assert state["user_initiated"] is True
        assert state["sessions_practiced"] == 1
        # initial 1.0 × user_initiated boost 2.0 / difficulty 1.0
        assert state["half_life_days"] == 2.0
        assert state["avg_response_latency_s"] == 3.0  # one 3s assistant→user delta

    def test_initial_half_life_scaled_by_pair_difficulty(self):
        strategy = _strategy(
            judgments=[PracticeJudgment(name="la comida", retrievals=1)],
            settings={"pair_difficulty": {"fr": 2.0, "*": 4.0}},
        )
        [assessment] = strategy.assess_items(
            PERSONA_ID, _conversation(), [_touched_concept("la comida", 1)]
        )
        assert assessment.persona_state["half_life_days"] == 0.5  # 1.0 / 2.0 (fr entry)

    def test_pair_difficulty_falls_back_to_star(self):
        strategy = _strategy(
            judgments=[PracticeJudgment(name="la comida", retrievals=1)],
            settings={"pair_difficulty": {"en": 1.0, "*": 4.0}},
            primary_language=Language("it"),
        )
        [assessment] = strategy.assess_items(
            PERSONA_ID, _conversation(), [_touched_concept("la comida", 1)]
        )
        assert assessment.persona_state["half_life_days"] == 0.25  # 1.0 / 4.0


class TestHalfLifeUpdates:
    def test_new_day_success_grows_half_life(self):
        stored = _stored_concept("la comida", 1, {
            "last_practiced_at": "2026-07-10", "half_life_days": 2.0,
            "retrievals": 3, "errors": 1, "avg_response_latency_s": 5.0,
            "user_initiated": False, "sessions_practiced": 2,
        })
        strategy = _strategy(
            judgments=[PracticeJudgment(name="la comida", retrievals=1)],
            stored=[stored],
        )
        [assessment] = strategy.assess_items(
            PERSONA_ID, _conversation(), [_touched_concept("la comida", 1)]
        )
        state = assessment.persona_state
        assert state["half_life_days"] == 4.0  # 2.0 × growth 2.0
        assert state["retrievals"] == 4
        assert state["sessions_practiced"] == 3
        assert state["last_practiced_at"] == "2026-07-12"
        # running latency average: (5.0 × 2 + 3.0) / 3
        assert abs(state["avg_response_latency_s"] - 13.0 / 3) < 1e-9

    def test_errors_shrink_half_life_and_win_over_successes(self):
        stored = _stored_concept("la comida", 1, {
            "last_practiced_at": "2026-07-10", "half_life_days": 2.0,
            "retrievals": 0, "errors": 0, "avg_response_latency_s": None,
            "user_initiated": False, "sessions_practiced": 1,
        })
        strategy = _strategy(
            judgments=[PracticeJudgment(name="la comida", retrievals=2, errors=1)],
            stored=[stored],
        )
        [assessment] = strategy.assess_items(
            PERSONA_ID, _conversation(), [_touched_concept("la comida", 1)]
        )
        assert assessment.persona_state["half_life_days"] == 1.0  # 2.0 × shrink 0.5
        assert assessment.persona_state["errors"] == 1

    def test_half_life_never_shrinks_below_floor(self):
        stored = _stored_concept("la comida", 1, {
            "last_practiced_at": "2026-07-10", "half_life_days": 0.6,
            "retrievals": 0, "errors": 0, "avg_response_latency_s": None,
            "user_initiated": False, "sessions_practiced": 1,
        })
        strategy = _strategy(
            judgments=[PracticeJudgment(name="la comida", errors=2)], stored=[stored],
        )
        [assessment] = strategy.assess_items(
            PERSONA_ID, _conversation(), [_touched_concept("la comida", 1)]
        )
        assert assessment.persona_state["half_life_days"] == 0.5

    def test_same_day_repetition_updates_counts_but_not_half_life(self):
        stored = _stored_concept("la comida", 1, {
            "last_practiced_at": "2026-07-12", "half_life_days": 2.0,
            "retrievals": 1, "errors": 0, "avg_response_latency_s": None,
            "user_initiated": False, "sessions_practiced": 1,
        })
        strategy = _strategy(
            judgments=[PracticeJudgment(name="la comida", retrievals=1)], stored=[stored],
        )
        [assessment] = strategy.assess_items(
            PERSONA_ID, _conversation(), [_touched_concept("la comida", 1)]
        )
        state = assessment.persona_state
        assert state["half_life_days"] == 2.0  # sleep-gated: no same-day growth
        assert state["retrievals"] == 2
        assert state["sessions_practiced"] == 2

    def test_user_initiated_is_sticky(self):
        stored = _stored_concept("la comida", 1, {
            "last_practiced_at": "2026-07-10", "half_life_days": 2.0,
            "retrievals": 1, "errors": 0, "avg_response_latency_s": None,
            "user_initiated": True, "sessions_practiced": 1,
        })
        strategy = _strategy(
            judgments=[PracticeJudgment(name="la comida", retrievals=1, user_initiated=False)],
            stored=[stored],
        )
        [assessment] = strategy.assess_items(
            PERSONA_ID, _conversation(), [_touched_concept("la comida", 1)]
        )
        assert assessment.persona_state["user_initiated"] is True


class TestExposureAndScope:
    def test_unjudged_item_records_exposure_only(self):
        stored = _stored_concept("la comida", 1, {
            "last_practiced_at": "2026-07-10", "half_life_days": 2.0,
            "retrievals": 3, "errors": 1, "avg_response_latency_s": None,
            "user_initiated": False, "sessions_practiced": 2,
        })
        strategy = _strategy(judgments=[], stored=[stored])  # judge failed / omitted item
        [assessment] = strategy.assess_items(
            PERSONA_ID, _conversation(), [_touched_concept("la comida", 1)]
        )
        state = assessment.persona_state
        assert state["retrievals"] == 3  # unchanged
        assert state["errors"] == 1  # unchanged
        assert state["half_life_days"] == 2.0  # unchanged
        assert state["last_practiced_at"] == "2026-07-12"  # day anchor still moves
        assert state["sessions_practiced"] == 3

    def test_episodes_and_idless_items_are_skipped(self):
        episode = Episode(id=5, summary="A trip.", happened_at=T0, origin_conversation_id=1)
        no_id = _touched_concept("sin id", 1)
        no_id.id = None
        strategy = _strategy(judgments=[])
        assert strategy.assess_items(PERSONA_ID, _conversation(), [episode, no_id]) == []

    def test_no_latency_when_conversation_has_no_assistant_user_pair(self):
        turns = [Turn(timestamp=T0, speaker=Speaker.USER, content="Hola.")]
        strategy = _strategy(judgments=[PracticeJudgment(name="hola", retrievals=1)])
        [assessment] = strategy.assess_items(
            PERSONA_ID, _conversation(turns), [_touched_concept("hola", 1)]
        )
        assert assessment.persona_state["avg_response_latency_s"] is None
