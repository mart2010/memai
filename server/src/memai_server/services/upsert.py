# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from enum import Enum
from uuid import UUID

from ..domain.model import Concept, EngagementLevel, Episode, MemoryType, Procedure
from .ports import (
    DisambiguationEvaluator,
    EmbeddingService,
    MemoryItem,
    MemoryRepository,
    MemorySynthesizer,
)


def _max_engagement(a: EngagementLevel, b: EngagementLevel) -> EngagementLevel:
    return a if a >= b else b


# Defaults match the values documented in CLAUDE.md's "Upsert similarity threshold"
# section — placeholders pending calibration against real usage data. Real values are
# read from [memory] in memai.toml (see infrastructure/config.py) and passed in by callers.
DEFAULT_MERGE_THRESHOLD = 0.93
DEFAULT_DISAMBIGUATE_THRESHOLD = 0.75

# Fetch a few nearest neighbors rather than just the top-1 so that, when a caller
# excludes some candidate ids (see exclude_ids below), a legitimate pre-existing match
# can still surface instead of being hidden behind an excluded one. No behavior change
# when exclude_ids is empty: candidates stay sorted by similarity, so the first entry is
# unchanged from a plain top_n=1 search.
_CANDIDATE_TOP_N = 5


class _MergeAction(Enum):
    AUTO_MERGE = "auto_merge"
    DISAMBIGUATE = "disambiguate"
    AUTO_INSERT = "auto_insert"


def _merge_action(similarity: float, merge_threshold: float, disambiguate_threshold: float) -> _MergeAction:
    if similarity >= merge_threshold:
        return _MergeAction.AUTO_MERGE
    if similarity >= disambiguate_threshold:
        return _MergeAction.DISAMBIGUATE
    return _MergeAction.AUTO_INSERT


def _existing_to_merge(
    candidates: list[tuple[float, MemoryItem]],
    candidate: MemoryItem,
    disambiguator: DisambiguationEvaluator,
    merge_threshold: float,
    disambiguate_threshold: float,
) -> MemoryItem | None:
    """Returns the existing record to merge into, or None to insert as new."""
    if not candidates:
        return None
    similarity, best = candidates[0]
    action = _merge_action(similarity, merge_threshold, disambiguate_threshold)
    if action == _MergeAction.AUTO_INSERT:
        return None
    if action == _MergeAction.AUTO_MERGE:
        return best
    return best if disambiguator.is_same(best, candidate) else None


class MemoryUpserter:
    """Shared merge-or-insert pipeline: embed → similarity search → two-tier threshold
    (auto-merge / LLM disambiguation / auto-insert) → LLM synthesis on merge → repository
    upsert. Used by both offline consolidation and the bundle installer — there is
    deliberately no separate insertion path, which is what makes bundle installs
    deduplicate against existing memory and reinstalls idempotent.

    `exclude_ids` (concept/procedure upserts only) lets a caller processing a *batch* of
    related items — currently only the bundle installer — keep items freshly inserted
    earlier in that same batch from being matched by later items in the same batch.
    Bundle authors write short, structurally similar but deliberately distinct items
    (e.g. "parlare"/"mangiare", both one-line "regular -are verb" definitions); without
    this, such siblings can cross the disambiguation threshold against each other and
    get blended, even though the author never intended them to be the same concept.
    It does NOT affect matching against genuinely pre-existing content (an earlier
    install, live-conversation extraction, or an earlier bundle) — that's the real,
    intended use of this pipeline's fuzzy matching. Live consolidation never passes
    this (default empty), so its behavior is unchanged.

    `allow_insert=False` and `update_description=False` (concept/procedure upserts only;
    both set together by ConsolidateMemory for personas with a registered
    PersonaAssessmentPort — today, only the language tutor) together mean this caller
    may only *recognize* a touch against existing content, never *author or edit* it:
    such a persona's own curated content (bundles) plus propose_items are the only
    sanctioned sources of new items or wording, so a live-conversation extraction pass
    can bump engagement (and fill a category gap) on a match, but a miss is discarded
    rather than inserted (item's `id` stays None as the "discarded" sentinel — callers
    must check before using it further, e.g. before feeding it to a
    PersonaAssessmentPort), and a match never runs the synthesizer or touches
    description/steps/embedding, even when the new text differs — a single
    conversation's phrasing must never drift a curated definition.

    Each upsert_* method mutates the passed item in place (description/summary,
    embedding, engagement, category, id) and returns True when it merged into an
    existing item, False when it inserted a new one (or when allow_insert=False and
    nothing was written — check `item.id is not None` to tell those two apart).
    """

    def __init__(
        self,
        memory_repo: MemoryRepository,
        embedding_service: EmbeddingService,
        disambiguator: DisambiguationEvaluator,
        synthesizer: MemorySynthesizer,
        merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
        disambiguate_threshold: float = DEFAULT_DISAMBIGUATE_THRESHOLD,
    ) -> None:
        self._memory_repo = memory_repo
        self._embedding_service = embedding_service
        self._disambiguator = disambiguator
        self._synthesizer = synthesizer
        self._merge_threshold = merge_threshold
        self._disambiguate_threshold = disambiguate_threshold

    def upsert_episode(self, episode: Episode) -> bool:
        episode.embedding = self._embedding_service.embed(episode.summary)
        candidates = self._memory_repo.search(episode.embedding, (MemoryType.EPISODE,), top_n=1)
        existing = self._find_existing(candidates, episode)
        if existing is not None:
            episode.summary = self._synthesizer.synthesize_episode(existing.summary, episode.summary)
            episode.embedding = self._embedding_service.embed(episode.summary)
            episode.id = existing.id
        episode.id = self._memory_repo.upsert_episode(episode)
        return existing is not None

    def upsert_concept(
        self,
        concept: Concept,
        persona_id: UUID,
        exclude_ids: frozenset[int] = frozenset(),
        allow_insert: bool = True,
        update_description: bool = True,
    ) -> bool:
        concept.embedding = self._embedding_service.embed(f"{concept.name}: {concept.description}")
        candidates = self._memory_repo.search(
            concept.embedding, (MemoryType.CONCEPT,), top_n=_CANDIDATE_TOP_N, persona_id=persona_id,
        )
        candidates = [c for c in candidates if c[1].id not in exclude_ids]
        existing = self._find_existing(candidates, concept)
        if existing is not None:
            if update_description:
                # Exact-duplicate short-circuit: same name + description needs no LLM
                # synthesis or re-embedding — this makes bundle reinstalls near-free.
                if not (existing.name == concept.name and existing.description == concept.description):
                    concept.description = self._synthesizer.synthesize_concept(existing, concept.description)
                    concept.embedding = self._embedding_service.embed(f"{concept.name}: {concept.description}")
            else:
                # This caller may only recognize a touch, never edit curated content
                # (e.g. the tutor's own extraction pass) — keep description/embedding
                # verbatim regardless of what the new text said.
                concept.description = existing.description
                concept.embedding = existing.embedding
            concept.engagement_level = _max_engagement(existing.engagement_level, concept.engagement_level)
            # An existing category (curated bundle content or an earlier
            # extraction) wins; the new extraction only fills a gap.
            concept.category = existing.category or concept.category
            concept.id = existing.id
        elif not allow_insert:
            # No match and this caller isn't allowed to author new content (e.g. the
            # tutor's own extraction pass — new tutor vocabulary only comes from bundles
            # or propose_items). Leave concept.id as None: the sentinel for "discarded,
            # never written" callers must check before using this item further.
            return False
        concept.id = self._memory_repo.upsert_concept(concept)
        return existing is not None

    def upsert_procedure(
        self,
        procedure: Procedure,
        persona_id: UUID,
        exclude_ids: frozenset[int] = frozenset(),
        allow_insert: bool = True,
        update_description: bool = True,
    ) -> bool:
        procedure.embedding = self._embedding_service.embed(f"{procedure.name}: {procedure.description}")
        candidates = self._memory_repo.search(
            procedure.embedding, (MemoryType.PROCEDURE,), top_n=_CANDIDATE_TOP_N, persona_id=persona_id,
        )
        candidates = [c for c in candidates if c[1].id not in exclude_ids]
        existing = self._find_existing(candidates, procedure)
        if existing is not None:
            if update_description:
                # Same exact-duplicate short-circuit as upsert_concept (steps included:
                # differing steps are new evidence and must go through synthesis).
                if not (
                    existing.name == procedure.name
                    and existing.description == procedure.description
                    and existing.steps == procedure.steps
                ):
                    procedure.description, procedure.steps = self._synthesizer.synthesize_procedure(
                        existing, procedure.description, procedure.steps
                    )
                    procedure.embedding = self._embedding_service.embed(f"{procedure.name}: {procedure.description}")
            else:
                procedure.description = existing.description
                procedure.steps = existing.steps
                procedure.embedding = existing.embedding
            procedure.engagement_level = _max_engagement(existing.engagement_level, procedure.engagement_level)
            procedure.category = existing.category or procedure.category
            procedure.id = existing.id
        elif not allow_insert:
            return False
        procedure.id = self._memory_repo.upsert_procedure(procedure)
        return existing is not None

    def _find_existing(self, candidates: list[tuple[float, MemoryItem]], candidate: MemoryItem) -> MemoryItem | None:
        return _existing_to_merge(
            candidates, candidate, self._disambiguator,
            self._merge_threshold, self._disambiguate_threshold,
        )
