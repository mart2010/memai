import pytest
from datetime import datetime, UTC
from uuid import uuid4

from memai_server.domain.model import AssistantPersona, GENERAL_ASSISTANT_ID, Language


class TestAssistantPersonaGuards:
    def test_non_system_persona_can_be_updated(self):
        """Spec: FR-204"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            languages=[], response_language=Language("en"), voices={"default": "af_heart"},
            is_system=False, created_at=now, updated_at=now,
        )
        later = datetime.now(UTC)
        persona.update(name="Expert Tutor", system_prompt="Teach rigorously.", updated_at=later)
        assert persona.name == "Expert Tutor"
        assert persona.system_prompt == "Teach rigorously."
        assert persona.updated_at == later

    def test_system_persona_can_be_updated(self):
        """Spec: FR-204"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="General Assistant", system_prompt="Help.",
            languages=[], response_language=Language("en"), voices={"default": "af_heart"},
            is_system=True, created_at=now, updated_at=now,
        )
        later = datetime.now(UTC)
        persona.update(name="Renamed Assistant", system_prompt="Help differently.", updated_at=later)
        assert persona.name == "Renamed Assistant"
        assert persona.system_prompt == "Help differently."
        assert persona.updated_at == later

    def test_update_can_change_voices_speaking_rate_response_language(self):
        """Spec: FR-204, FR-105"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            languages=[], response_language=Language("en"), voices={"default": "af_heart"},
            is_system=False, created_at=now, updated_at=now,
        )
        persona.update(
            updated_at=datetime.now(UTC),
            voices={"default": "ff_siwis", "target_teacher": "ef_dora"},
            speaking_rate=0.8,
            response_language=Language("fr"),
        )
        assert persona.default_voice == "ff_siwis"
        assert persona.voices["target_teacher"] == "ef_dora"
        assert persona.speaking_rate == 0.8
        assert persona.response_language == Language("fr")

    def test_voices_must_include_default_role(self):
        """Spec: INV-7"""
        now = datetime.now(UTC)
        with pytest.raises(ValueError, match="default"):
            AssistantPersona(
                id=uuid4(), name="Tutor", system_prompt="Teach me.",
                languages=[], response_language=Language("en"), voices={"narrator": "af_heart"},
                is_system=False, created_at=now, updated_at=now,
            )

    def test_default_voice_must_be_single_no_rotation_pool(self):
        """Spec: INV-7"""
        # The default role is the fixed anchor; "|" rotation pools (HVPT) are only
        # valid on additional roles.
        now = datetime.now(UTC)
        with pytest.raises(ValueError, match="single voice"):
            AssistantPersona(
                id=uuid4(), name="Tutor", system_prompt="Teach me.",
                languages=[], response_language=Language("en"),
                voices={"default": "af_heart|ff_siwis"},
                is_system=False, created_at=now, updated_at=now,
            )

    def test_additional_role_may_carry_rotation_pool(self):
        """Spec: FR-206, INV-7"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            languages=[], response_language=Language("en"),
            voices={"default": "ff_siwis", "target_teacher": "ef_dora|em_alex"},
            is_system=False, created_at=now, updated_at=now,
        )
        assert persona.voices["target_teacher"] == "ef_dora|em_alex"

    def test_update_rejects_pool_on_default_role(self):
        """Spec: INV-7"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            languages=[], response_language=Language("en"), voices={"default": "af_heart"},
            is_system=False, created_at=now, updated_at=now,
        )
        with pytest.raises(ValueError, match="single voice"):
            persona.update(updated_at=datetime.now(UTC), voices={"default": "a|b"})

    def test_update_rejects_voices_without_default_role(self):
        """Spec: INV-7"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            languages=[], response_language=Language("en"), voices={"default": "af_heart"},
            is_system=False, created_at=now, updated_at=now,
        )
        with pytest.raises(ValueError, match="default"):
            persona.update(updated_at=datetime.now(UTC), voices={"narrator": "ff_siwis"})
        assert persona.default_voice == "af_heart"

    def test_partial_update_preserves_unchanged_fields(self):
        """Spec: FR-204"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Original prompt.",
            languages=[], response_language=Language("en"), voices={"default": "af_heart"},
            is_system=False, created_at=now, updated_at=now,
        )
        persona.update(name="New Name", updated_at=datetime.now(UTC))
        assert persona.system_prompt == "Original prompt."

    def test_general_assistant_factory(self):
        """Spec: FR-201, TR-506"""
        persona = AssistantPersona.general_assistant("You are a helpful assistant.")
        assert persona.id == GENERAL_ASSISTANT_ID
        assert persona.name == "Vocal Assistant"
        assert persona.is_system is True
        assert persona.system_prompt == "You are a helpful assistant."
        assert persona.speaking_rate == 1.0
        assert persona.is_active is True
        assert persona.persona_key is None
        assert persona.settings is None

    def test_persona_key_and_settings_default_to_none(self):
        """Spec: TR-506"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            languages=[], response_language=Language("en"), voices={"default": "af_heart"},
            is_system=False, created_at=now, updated_at=now,
        )
        assert persona.persona_key is None
        assert persona.settings is None

    def test_bundle_installed_persona_carries_key_and_settings(self):
        """Spec: TR-506, TR-903"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Profesora Sofía", system_prompt="Teach Spanish.",
            languages=[Language("es")], response_language=Language("es"),
            voices={"default": "ff_siwis", "target_teacher": "ef_dora"},
            is_system=False, created_at=now, updated_at=now,
            persona_key="meo/spanish-tutor",
            settings={"elicitation_cap": 2, "pair_difficulty": {"en": 1.0, "*": 1.5}},
        )
        assert persona.persona_key == "meo/spanish-tutor"
        assert persona.settings["elicitation_cap"] == 2

    def test_non_system_persona_can_be_deactivated_and_reactivated(self):
        """Spec: FR-204"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach me.",
            languages=[], response_language=Language("en"), voices={"default": "af_heart"},
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
        """Spec: FR-201"""
        now = datetime.now(UTC)
        persona = AssistantPersona(
            id=uuid4(), name="General Assistant", system_prompt="Help.",
            languages=[], response_language=Language("en"), voices={"default": "af_heart"},
            is_system=True, created_at=now, updated_at=now,
        )
        with pytest.raises(ValueError, match="System personas"):
            persona.deactivate(updated_at=datetime.now(UTC))
