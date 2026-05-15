import pytest
from datetime import datetime, UTC
from uuid import uuid4

from memai_server.domain.model import AssistantPersona, GENERAL_ASSISTANT_ID, Language, User
from memai_server.services.persona import CreatePersona, EditPersona, ListPersonas, RemovePersona, SwitchPersona
from memai_server.services.session import SessionContext

from tests.fakes.fakes import FakePersonaRepository


def _now() -> datetime:
    return datetime.now(UTC)


def _general_assistant() -> AssistantPersona:
    return AssistantPersona.general_assistant("You are a helpful assistant.")


def _other_persona(name: str = "Coach") -> AssistantPersona:
    return AssistantPersona(
        id=uuid4(),
        name=name,
        system_prompt="You are a coach.",
        languages=[],
        is_system=False,
        created_at=_now(),
        updated_at=_now(),
    )


def _session(active_persona: AssistantPersona) -> SessionContext:
    return SessionContext(
        session_id=uuid4(),
        started_at=_now(),
        user=User(id=uuid4(), primary_language=Language("en")),
        active_persona=active_persona,
        memory_brief=None,
    )


def _repo_with(*personas: AssistantPersona) -> FakePersonaRepository:
    repo = FakePersonaRepository()
    for p in personas:
        repo.save(p)
    return repo


class TestCreatePersona:
    def test_creates_persona_when_general_assistant_active(self):
        ga = _general_assistant()
        session = _session(ga)
        use_case = CreatePersona(_repo_with(ga))
        persona = use_case.execute(session, name="Coach", system_prompt="You coach.", now=_now())
        assert persona.name == "Coach"
        assert not persona.is_system

    def test_raises_when_non_general_persona_active(self):
        coach = _other_persona()
        session = _session(coach)
        use_case = CreatePersona(_repo_with(coach))
        with pytest.raises(ValueError, match="GeneralAssistant"):
            use_case.execute(session, name="Tutor", system_prompt="You teach.", now=_now())

    def test_saved_to_repo(self):
        ga = _general_assistant()
        session = _session(ga)
        repo = _repo_with(ga)
        use_case = CreatePersona(repo)
        persona = use_case.execute(session, name="Coach", system_prompt="You coach.", now=_now())
        assert repo.get(persona.id) is not None


class TestListPersonas:
    def test_returns_all_personas(self):
        ga = _general_assistant()
        coach = _other_persona("Coach")
        use_case = ListPersonas(_repo_with(ga, coach))
        result = use_case.execute()
        assert len(result) == 2

    def test_empty_repo(self):
        use_case = ListPersonas(FakePersonaRepository())
        assert use_case.execute() == []


class TestEditPersona:
    def test_updates_name_and_prompt(self):
        coach = _other_persona("Coach")
        repo = _repo_with(coach)
        use_case = EditPersona(repo)
        updated = use_case.execute(coach.id, now=_now(), name="Mentor", system_prompt="You mentor.")
        assert updated.name == "Mentor"
        assert updated.system_prompt == "You mentor."

    def test_partial_update_preserves_other_fields(self):
        coach = _other_persona("Coach")
        original_prompt = coach.system_prompt
        repo = _repo_with(coach)
        use_case = EditPersona(repo)
        updated = use_case.execute(coach.id, now=_now(), name="Mentor")
        assert updated.system_prompt == original_prompt

    def test_raises_on_unknown_persona(self):
        use_case = EditPersona(FakePersonaRepository())
        with pytest.raises(ValueError):
            use_case.execute(uuid4(), now=_now(), name="X")


class TestRemovePersona:
    def test_removes_non_system_persona(self):
        coach = _other_persona("Coach")
        repo = _repo_with(coach)
        RemovePersona(repo).execute(coach.id)
        assert repo.get(coach.id) is None

    def test_raises_on_system_persona(self):
        ga = _general_assistant()
        repo = _repo_with(ga)
        with pytest.raises(ValueError, match="[Ss]ystem"):
            RemovePersona(repo).execute(ga.id)

    def test_raises_on_unknown_persona(self):
        with pytest.raises(ValueError):
            RemovePersona(FakePersonaRepository()).execute(uuid4())


class TestSwitchPersona:
    def test_returns_event_with_correct_ids(self):
        ga = _general_assistant()
        coach = _other_persona("Coach")
        session = _session(ga)
        repo = _repo_with(ga, coach)
        event = SwitchPersona(repo).execute(session, coach.id)
        assert event.from_persona_id == GENERAL_ASSISTANT_ID
        assert event.to_persona_id == coach.id

    def test_updates_session_active_persona(self):
        ga = _general_assistant()
        coach = _other_persona("Coach")
        session = _session(ga)
        SwitchPersona(_repo_with(ga, coach)).execute(session, coach.id)
        assert session.active_persona.id == coach.id

    def test_raises_on_unknown_persona(self):
        ga = _general_assistant()
        session = _session(ga)
        with pytest.raises(ValueError):
            SwitchPersona(FakePersonaRepository()).execute(session, uuid4())
