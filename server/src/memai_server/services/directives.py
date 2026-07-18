# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from datetime import datetime, UTC
from uuid import UUID

from ..domain.model import GENERAL_ASSISTANT_ID, AssistantPersona, Concept, Language
from .ports import EmbeddingService, MemoryRepository

# Canonical trigger phrasing (FR-207) — deliberately the exact text embedded into each
# Directive concept below, so a user utterance matching the documented phrasing (see
# docs/PERSONAS.md's "Switching personas by voice" section, kept in sync with these by
# hand) anchors tightly in embedding space rather than relying on loose topical
# similarity. Two
# phrasings per persona broadens match coverage without inventing a multi-phrase
# concept — each phrasing is its own Concept row sharing the same `directive` payload.
# English-only for now (matches the FAQ) — per-primary-language phrasing is a natural
# future extension, out of scope for this pass.
_SWITCH_TO_TEMPLATES = ("Switch me to {name}.", "Switch to {name}.")
_RETURN_TO_GA_PHRASES = (
    "Switch me back to the general assistant.",
    "Switch back to the general assistant.",
)
_PHRASING_LANGUAGE = Language("en")


class PersonaDirectiveSync:
    """Keeps GA-owned Directive concepts (FR-207) in sync with persona lifecycle.
    Deliberately bypasses MemoryUpserter/the merge-disambiguation pipeline used for
    regular bundle content — directive concepts are deterministic operational data,
    not extracted/authored knowledge subject to near-duplicate merging."""

    def __init__(self, memory_repo: MemoryRepository, embedding_service: EmbeddingService) -> None:
        self._memory_repo = memory_repo
        self._embedding_service = embedding_service

    def _create_switch_directive(self, name: str, target_persona_id: UUID, templates: tuple[str, ...]) -> None:
        now = datetime.now(UTC)
        directive = {"action": "switch_persona", "target_persona_id": str(target_persona_id)}
        for template in templates:
            phrase = template.format(name=name)
            self._memory_repo.upsert_concept(Concept(
                id=None,
                persona_id=GENERAL_ASSISTANT_ID,
                name=f"Switch to {name}",
                description=phrase,
                language=_PHRASING_LANGUAGE,
                directive=directive,
                created_at=now,
                updated_at=now,
                embedding=self._embedding_service.embed(phrase),
            ))

    def sync_created(self, persona: AssistantPersona) -> None:
        """Idempotent: a persona that already has a "switch to me" directive (e.g. a
        bundle reinstall onto an existing persona) is left alone."""
        target = str(persona.id)
        existing = self._memory_repo.list_directives(GENERAL_ASSISTANT_ID)
        if any((d.directive or {}).get("target_persona_id") == target for d in existing):
            return
        self._create_switch_directive(persona.name, persona.id, _SWITCH_TO_TEMPLATES)

    def sync_removed(self, persona_id: UUID) -> None:
        target = str(persona_id)
        for d in self._memory_repo.list_directives(GENERAL_ASSISTANT_ID):
            if d.id is not None and (d.directive or {}).get("target_persona_id") == target:
                self._memory_repo.delete_concept(d.id)

    def ensure_return_to_general_assistant(self) -> None:
        """Idempotent — safe to call on every server startup."""
        target = str(GENERAL_ASSISTANT_ID)
        existing = self._memory_repo.list_directives(GENERAL_ASSISTANT_ID)
        if any((d.directive or {}).get("target_persona_id") == target for d in existing):
            return
        self._create_switch_directive("the general assistant", GENERAL_ASSISTANT_ID, _RETURN_TO_GA_PHRASES)
