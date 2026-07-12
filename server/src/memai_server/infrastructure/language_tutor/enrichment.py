# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Language-tutor enrichment strategy — the first concrete PersonaEnrichmentPort.

Offline. Bundles stay the curriculum backbone; this strategy only supplements them
when consolidation shows a real interest signal: several USER-INITIATED concepts
clustering around a theme (docs/BRIEF_phase12_tutor.md — the interest-cluster
reopening). It then proposes the surrounding vocabulary cluster as UNSEEN drafts.
Exclusion of already-known material is delegated to the shared upsert-merge dedup —
proposals that duplicate existing memory merge into it instead of inserting.
"""
from collections.abc import Sequence
from collections import Counter
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from ...domain.model import Concept, EngagementLevel, Language, MemoryType
from ...services.ports import MemoryItemDraft, MemoryRepository, PersonaRepository
from .selection import _cosine
from .state import STATE_USER_INITIATED

# AssistantPersona.settings keys with defaults — calibration placeholders.
SETTING_CLUSTER_THRESHOLD = "interest_cluster_threshold"  # min cosine to join a cluster
SETTING_CLUSTER_MIN_SIZE = "interest_cluster_min_size"    # "several" user-initiated items
SETTING_ENRICHMENT_BATCH = "enrichment_batch_size"        # drafts proposed per run

DEFAULT_CLUSTER_THRESHOLD = 0.55
DEFAULT_CLUSTER_MIN_SIZE = 3
DEFAULT_ENRICHMENT_BATCH = 5


@dataclass(frozen=True)
class ProposedItem:
    """One LLM-proposed vocabulary item surrounding an interest cluster."""
    name: str
    description: str
    category: str | None = None


class ClusterProposer(Protocol):
    def propose(
        self, language: Language, cluster: Sequence[Concept], count: int
    ) -> Sequence[ProposedItem]: ...


def _clusters(seeds: list[Concept], threshold: float) -> list[list[Concept]]:
    """Greedy single-pass clustering: each unassigned seed opens a cluster and absorbs
    every remaining seed within `threshold` cosine similarity. Crude but sufficient —
    the goal is 'do several user-initiated items share a theme', not taxonomy."""
    unassigned = list(seeds)
    clusters: list[list[Concept]] = []
    while unassigned:
        anchor = unassigned.pop(0)
        cluster = [anchor]
        rest: list[Concept] = []
        for other in unassigned:
            if _cosine(anchor.embedding, other.embedding) >= threshold:
                cluster.append(other)
            else:
                rest.append(other)
        unassigned = rest
        clusters.append(cluster)
    return clusters


class LanguageTutorEnrichmentStrategy:
    """Implements PersonaEnrichmentPort for every language-tutor persona."""

    def __init__(
        self,
        memory_repo: MemoryRepository,
        persona_repo: PersonaRepository,
        proposer: ClusterProposer,
    ) -> None:
        self._memory_repo = memory_repo
        self._persona_repo = persona_repo
        self._proposer = proposer

    def propose_items(self, persona_id: UUID) -> Sequence[MemoryItemDraft]:
        persona = self._persona_repo.get(persona_id)
        settings = (persona.settings if persona else None) or {}
        threshold = float(settings.get(SETTING_CLUSTER_THRESHOLD, DEFAULT_CLUSTER_THRESHOLD))
        min_size = int(settings.get(SETTING_CLUSTER_MIN_SIZE, DEFAULT_CLUSTER_MIN_SIZE))
        batch = int(settings.get(SETTING_ENRICHMENT_BATCH, DEFAULT_ENRICHMENT_BATCH))

        # Interest signal = user-initiated concepts (assessment-written salience flag).
        # Vocabulary clusters are a Concept phenomenon — procedures are not scanned.
        items = self._memory_repo.list_items(persona_id, (MemoryType.CONCEPT,))
        seeds = [
            i for i in items
            if isinstance(i, Concept)
            and i.embedding
            and (i.persona_state or {}).get(STATE_USER_INITIATED)
        ]
        if len(seeds) < min_size:
            return []

        qualifying = [c for c in _clusters(seeds, threshold) if len(c) >= min_size]
        if not qualifying:
            return []
        # One cluster per run — enrichment is a trickle beside the bundle backbone.
        cluster = max(qualifying, key=len)

        # The cluster's own language wins (its items were introduced in it); majority
        # vote guards against a stray other-language seed.
        language = Counter(i.language for i in cluster).most_common(1)[0][0]
        proposals = self._proposer.propose(language, cluster, batch)
        return [
            Concept(
                id=None,
                persona_id=persona_id,
                name=p.name,
                description=p.description,
                language=language,
                category=p.category,
                engagement_level=EngagementLevel.UNSEEN,
            )
            for p in proposals
        ]
