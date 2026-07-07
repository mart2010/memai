import pytest
from datetime import datetime, UTC
from uuid import uuid4

from memai_server.domain.model import AssistantPersona, GENERAL_ASSISTANT_ID, Language


class TestAssistantPersonaGuards:
    def test_non_system_persona_can_be_updated(self):
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            languages=[], response_language=Language("en"), tts_voice="af_heart",
            is_system=False, created_at=now, updated_at=now,
        )
        later = datetime.now(UTC)
        persona.update(name="Expert Tutor", system_prompt="Teach rigorously.", updated_at=later)
        assert persona.name == "Expert Tutor"
        assert persona.system_prompt == "Teach rigorously."
        assert persona.updated_at == later

    def test_system_persona_can_be_updated(self):
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="General Assistant", system_prompt="Help.",
            languages=[], response_language=Language("en"), tts_voice="af_heart",
            is_system=True, created_at=now, updated_at=now,
        )
        later = datetime.now(UTC)
        persona.update(name="Renamed Assistant", system_prompt="Help differently.", updated_at=later)
        assert persona.name == "Renamed Assistant"
        assert persona.system_prompt == "Help differently."
        assert persona.updated_at == later

    def test_update_can_change_tts_voice_speaking_rate_response_language(self):
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            languages=[], response_language=Language("en"), tts_voice="af_heart",
            is_system=False, created_at=now, updated_at=now,
        )
        persona.update(
            updated_at=datetime.now(UTC),
            tts_voice="ff_siwis",
            speaking_rate=0.8,
            response_language=Language("fr"),
        )
        assert persona.tts_voice == "ff_siwis"
        assert persona.speaking_rate == 0.8
        assert persona.response_language == Language("fr")

    def test_partial_update_preserves_unchanged_fields(self):
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Original prompt.",
            languages=[], response_language=Language("en"), tts_voice="af_heart",
            is_system=False, created_at=now, updated_at=now,
        )
        persona.update(name="New Name", updated_at=datetime.now(UTC))
        assert persona.system_prompt == "Original prompt."

    def test_general_assistant_factory(self):
        persona = AssistantPersona.general_assistant("You are a helpful assistant.")
        assert persona.id == GENERAL_ASSISTANT_ID
        assert persona.name == "Vocal Assistant"
        assert persona.is_system is True
        assert persona.system_prompt == "You are a helpful assistant."
        assert persona.speaking_rate == 1.0
        assert persona.is_active is True

    def test_non_system_persona_can_be_deactivated_and_reactivated(self):
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            languages=[], response_language=Language("en"), tts_voice="af_heart",
            is_system=False, created_at=now, updated_at=now,
        )
        later = datetime.now(UTC)
        persona.deactivate(updated_at=later)
        assert persona.is_active is False
        assert persona.updated_at == later

        even_later = datetime.now(UTC)
        persona.reactivate(updated_at=even_later)
        assert persona.is_active is True
        assert persona.updated_at == even_later

    def test_system_persona_cannot_be_deactivated(self):
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="General Assistant", system_prompt="Help.",
            languages=[], response_language=Language("en"), tts_voice="af_heart",
            is_system=True, created_at=now, updated_at=now,
        )
        with pytest.raises(ValueError, match="System personas"):
            persona.deactivate(updated_at=datetime.now(UTC))
