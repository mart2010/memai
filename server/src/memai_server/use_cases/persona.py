from datetime import datetime
from uuid import UUID, uuid4

from ..domain.events import PersonaSwitched
from ..domain.model import AssistantPersona, GENERAL_ASSISTANT_ID
from .ports import PersonaRepository
from .session import SessionContext


class CreatePersona:
    def __init__(self, persona_repo: PersonaRepository) -> None:
        self._repo = persona_repo

    def execute(self, session: SessionContext, name: str, system_prompt: str, now: datetime) -> AssistantPersona:
        if session.live_conversation.persona_id != GENERAL_ASSISTANT_ID:
            raise ValueError("Persona management is only available when GeneralAssistant is active")
        persona = AssistantPersona(
            id=uuid4(),
            name=name,
            system_prompt=system_prompt,
            is_system=False,
            created_at=now,
            updated_at=now,
        )
        self._repo.save(persona)
        return persona


class ListPersonas:
    def __init__(self, persona_repo: PersonaRepository) -> None:
        self._repo = persona_repo

    def execute(self) -> list[AssistantPersona]:
        return self._repo.list_all()


class EditPersona:
    def __init__(self, persona_repo: PersonaRepository) -> None:
        self._repo = persona_repo

    def execute(
        self,
        persona_id: UUID,
        now: datetime,
        name: str | None = None,
        system_prompt: str | None = None,
    ) -> AssistantPersona:
        persona = self._repo.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona {persona_id} not found")
        persona.update(updated_at=now, name=name, system_prompt=system_prompt)
        self._repo.save(persona)
        return persona


class RemovePersona:
    def __init__(self, persona_repo: PersonaRepository) -> None:
        self._repo = persona_repo

    def execute(self, persona_id: UUID) -> None:
        persona = self._repo.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona {persona_id} not found")
        if persona.is_system:
            raise ValueError("System personas cannot be removed")
        self._repo.delete(persona_id)


class SwitchPersona:
    def __init__(self, persona_repo: PersonaRepository) -> None:
        self._repo = persona_repo

    def execute(self, session: SessionContext, persona_id: UUID) -> PersonaSwitched:
        persona = self._repo.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona {persona_id} not found")
        event = PersonaSwitched(
            from_persona_id=session.live_conversation.persona_id,
            to_persona_id=persona_id,
        )
        session.live_conversation.persona_id = persona_id
        session.active_persona = persona
        return event
