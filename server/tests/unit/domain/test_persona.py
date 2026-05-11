import pytest
from datetime import datetime, UTC
from uuid import uuid4

from memai_server.domain.model import AssistantPersona, GENERAL_ASSISTANT_ID


class TestAssistantPersonaGuards:
    def test_non_system_persona_can_be_updated(self):
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            is_system=False, created_at=now, updated_at=now,
        )
        later = datetime.now(UTC)
        persona.update(name="Expert Tutor", system_prompt="Teach rigorously.", updated_at=later)
        assert persona.name == "Expert Tutor"
        assert persona.system_prompt == "Teach rigorously."
        assert persona.updated_at == later

    def test_system_persona_cannot_be_updated(self):
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="General Assistant", system_prompt="Help.",
            is_system=True, created_at=now, updated_at=now,
        )
        with pytest.raises(ValueError, match="System personas"):
            persona.update(name="Hacked", updated_at=datetime.now(UTC))

    def test_partial_update_preserves_unchanged_fields(self):
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Original prompt.",
            is_system=False, created_at=now, updated_at=now,
        )
        persona.update(name="New Name", updated_at=datetime.now(UTC))
        assert persona.system_prompt == "Original prompt."

    def test_general_assistant_factory(self):
        persona = AssistantPersona.general_assistant("You are a helpful assistant.")
        assert persona.id == GENERAL_ASSISTANT_ID
        assert persona.name == "General Assistant"
        assert persona.is_system is True
        assert persona.system_prompt == "You are a helpful assistant."
