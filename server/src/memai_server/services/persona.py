# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from datetime import datetime
from uuid import UUID, uuid4

from ..domain.events import PersonaDeactivated, PersonaReactivated, PersonaSwitched
from ..domain.model import AssistantPersona, DEFAULT_VOICE_ROLE, GENERAL_ASSISTANT_ID, Language
from .directives import PersonaDirectiveSync
from .ports import PersonaRepository
from .session import WorkingMemory


class CreatePersona:
    def __init__(self, persona_repo: PersonaRepository, directive_sync: PersonaDirectiveSync) -> None:
        self._repo = persona_repo
        self._directive_sync = directive_sync

    def execute(
        self,
        session: WorkingMemory,
        name: str,
        system_prompt: str,
        now: datetime,
        response_language: Language | None = None,
        voices: dict[str, str] | None = None,
        speaking_rate: float = 1.0,
        languages: list[Language] | None = None,
    ) -> AssistantPersona:
        if session.active_persona.id != GENERAL_ASSISTANT_ID:
            raise ValueError("Persona management is only available when GeneralAssistant is active")
        lang = response_language or session.user.primary_language or Language("en")
        persona = AssistantPersona(
            id=uuid4(),
            name=name,
            system_prompt=system_prompt,
            languages=languages or [],
            response_language=lang,
            voices=voices or {DEFAULT_VOICE_ROLE: "af_heart"},
            speaking_rate=speaking_rate,
            is_system=False,
            created_at=now,
            updated_at=now,
        )
        self._repo.save(persona)
        # A Directive (FR-207) is how the user actually reaches this persona going
        # forward — create it in the same use case that creates the persona itself.
        self._directive_sync.sync_created(persona)
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
        voices: dict[str, str] | None = None,
        speaking_rate: float | None = None,
        response_language: Language | None = None,
    ) -> AssistantPersona:
        persona = self._repo.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona {persona_id} not found")
        persona.update(
            updated_at=now,
            name=name,
            system_prompt=system_prompt,
            voices=voices,
            speaking_rate=speaking_rate,
            response_language=response_language,
        )
        self._repo.save(persona)
        return persona


class RemovePersona:
    def __init__(self, persona_repo: PersonaRepository, directive_sync: PersonaDirectiveSync) -> None:
        self._repo = persona_repo
        self._directive_sync = directive_sync

    def execute(self, persona_id: UUID) -> None:
        persona = self._repo.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona {persona_id} not found")
        if persona.is_system:
            raise ValueError("System personas cannot be removed")
        # INV-9's cascade only cleans up this persona's OWN concepts/procedures — its
        # "switch to me" Directive is GA-owned, so it needs this explicit cleanup.
        self._directive_sync.sync_removed(persona_id)
        self._repo.delete(persona_id)


class DeactivatePersona:
    def __init__(self, persona_repo: PersonaRepository) -> None:
        self._repo = persona_repo

    def execute(self, persona_id: UUID, now: datetime) -> PersonaDeactivated:
        persona = self._repo.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona {persona_id} not found")
        persona.deactivate(updated_at=now)
        self._repo.save(persona)
        return PersonaDeactivated(persona_id=persona_id)


class ReactivatePersona:
    def __init__(self, persona_repo: PersonaRepository) -> None:
        self._repo = persona_repo

    def execute(self, persona_id: UUID, now: datetime) -> PersonaReactivated:
        persona = self._repo.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona {persona_id} not found")
        persona.reactivate(updated_at=now)
        self._repo.save(persona)
        return PersonaReactivated(persona_id=persona_id)


class SwitchPersona:
    def __init__(self, persona_repo: PersonaRepository) -> None:
        self._repo = persona_repo

    def execute(self, session: WorkingMemory, persona_id: UUID) -> PersonaSwitched:
        persona = self._repo.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona {persona_id} not found")
        event = PersonaSwitched(
            from_persona_id=session.active_persona.id,
            to_persona_id=persona_id,
        )
        session.active_persona = persona
        return event
