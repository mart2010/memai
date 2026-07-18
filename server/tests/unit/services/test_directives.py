from datetime import datetime, UTC
from uuid import uuid4

from memai_server.domain.model import GENERAL_ASSISTANT_ID, AssistantPersona, Language
from memai_server.services.directives import PersonaDirectiveSync

from tests.fakes.fakes import FakeEmbeddingService, FakeMemoryRepository


def _now() -> datetime:
    return datetime.now(UTC)


def _persona(name: str = "Tutor") -> AssistantPersona:
    return AssistantPersona(
        id=uuid4(), name=name, system_prompt="You teach.", languages=[],
        response_language=Language("en"), voices={"default": "af_heart"},
        is_system=False, created_at=_now(), updated_at=_now(),
    )


class TestSyncCreated:
    def test_creates_switch_directive_concepts(self):
        """Spec: FR-207"""
        memory_repo = FakeMemoryRepository()
        sync = PersonaDirectiveSync(memory_repo, FakeEmbeddingService())
        persona = _persona("Tutor Italiano")

        sync.sync_created(persona)

        directives = memory_repo.list_directives(GENERAL_ASSISTANT_ID)
        assert directives
        assert all(d.persona_id == GENERAL_ASSISTANT_ID for d in directives)
        assert all(d.directive == {"action": "switch_persona", "target_persona_id": str(persona.id)} for d in directives)
        assert all(d.embedding is not None for d in directives)
        # Canonical phrasing: what the FAQ documents is exactly what's embedded.
        assert any("Tutor Italiano" in d.description for d in directives)

    def test_idempotent_on_repeated_call(self):
        """Spec: FR-207 — a bundle reinstall onto an existing persona must not
        duplicate its directive concepts."""
        memory_repo = FakeMemoryRepository()
        sync = PersonaDirectiveSync(memory_repo, FakeEmbeddingService())
        persona = _persona()

        sync.sync_created(persona)
        first_count = len(memory_repo.list_directives(GENERAL_ASSISTANT_ID))
        sync.sync_created(persona)

        assert len(memory_repo.list_directives(GENERAL_ASSISTANT_ID)) == first_count

    def test_different_personas_get_independent_directives(self):
        """Spec: FR-207"""
        memory_repo = FakeMemoryRepository()
        sync = PersonaDirectiveSync(memory_repo, FakeEmbeddingService())
        a, b = _persona("Coach"), _persona("Tutor")

        sync.sync_created(a)
        sync.sync_created(b)

        directives = memory_repo.list_directives(GENERAL_ASSISTANT_ID)
        targets = {d.directive["target_persona_id"] for d in directives}
        assert targets == {str(a.id), str(b.id)}


class TestSyncRemoved:
    def test_removes_only_the_targeted_persona_directive(self):
        """Spec: FR-207, INV-9"""
        memory_repo = FakeMemoryRepository()
        sync = PersonaDirectiveSync(memory_repo, FakeEmbeddingService())
        a, b = _persona("Coach"), _persona("Tutor")
        sync.sync_created(a)
        sync.sync_created(b)

        sync.sync_removed(a.id)

        remaining = memory_repo.list_directives(GENERAL_ASSISTANT_ID)
        assert remaining
        assert all(d.directive["target_persona_id"] == str(b.id) for d in remaining)

    def test_no_op_when_nothing_to_remove(self):
        """Spec: FR-207"""
        memory_repo = FakeMemoryRepository()
        sync = PersonaDirectiveSync(memory_repo, FakeEmbeddingService())
        sync.sync_removed(uuid4())  # must not raise
        assert memory_repo.list_directives(GENERAL_ASSISTANT_ID) == []


class TestEnsureReturnToGeneralAssistant:
    def test_creates_return_directive(self):
        """Spec: FR-207"""
        memory_repo = FakeMemoryRepository()
        sync = PersonaDirectiveSync(memory_repo, FakeEmbeddingService())

        sync.ensure_return_to_general_assistant()

        directives = memory_repo.list_directives(GENERAL_ASSISTANT_ID)
        assert directives
        assert all(d.directive["target_persona_id"] == str(GENERAL_ASSISTANT_ID) for d in directives)

    def test_idempotent_across_repeated_startups(self):
        """Spec: FR-207 — safe to call on every server startup."""
        memory_repo = FakeMemoryRepository()
        sync = PersonaDirectiveSync(memory_repo, FakeEmbeddingService())

        sync.ensure_return_to_general_assistant()
        first_count = len(memory_repo.list_directives(GENERAL_ASSISTANT_ID))
        sync.ensure_return_to_general_assistant()

        assert len(memory_repo.list_directives(GENERAL_ASSISTANT_ID)) == first_count
