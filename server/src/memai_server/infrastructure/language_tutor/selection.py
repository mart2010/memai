# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Language-tutor selection strategy — the first concrete PersonaSelectionPort.

Batch composition (docs/BRIEF_phase12_tutor.md): review items ranked by due-ness,
new items in curriculum order, interleaved by category (anti-blocking), each paired
with a related Episode via similarity search — or a capped elicitation hint when no
episode matches. A free-text `focus` (the user's session wish, carried verbatim from
the [FOCUS: ...] marker) steers the batch; focus=None is the default learning path.
"""
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, UTC
from math import sqrt
from typing import Protocol
from uuid import UUID

from ...domain.model import EngagementLevel, MemoryType
from ...services.ports import (
    EmbeddingService,
    MemoryItem,
    MemoryRepository,
    PersonaRepository,
    SelectedItem,
)

from .state import STATE_HALF_LIFE_DAYS, STATE_LAST_PRACTICED_AT

STRATEGY_NAME = "language_tutor"

# AssistantPersona.settings keys (tutor vocabulary, opaque to generic code) and their
# defaults. All numeric defaults are placeholders pending calibration on real usage —
# same instrument-now-calibrate-later posture as the upsert thresholds.
SETTING_RANKING = "ranking"                        # "engagement" (default) | "retention"
SETTING_REVIEW_SHARE = "batch_review_share"        # fraction of the batch for review items
SETTING_ANCHOR_THRESHOLD = "episode_anchor_threshold"  # min similarity for an episode anchor
SETTING_ELICITATION_CAP = "elicitation_cap"        # max elicitation hints per batch

DEFAULT_REVIEW_SHARE = 0.5
DEFAULT_ANCHOR_THRESHOLD = 0.6
DEFAULT_ELICITATION_CAP = 2

_EPOCH = datetime.fromtimestamp(0, UTC)

FOCUS_MODES = ("review", "new", "mixed")


@dataclass(frozen=True)
class TutorFocus:
    """Structured reading of the user's free-text session wish."""
    mode: str = "mixed"          # "review" | "new" | "mixed"
    category: str | None = None  # one of the persona's own category values, or None
    topic: str | None = None     # free-text theme to rank items against, or None


class FocusInterpreter(Protocol):
    """Maps the verbatim focus text to TutorFocus. `categories` lists the category
    values actually present in the persona's memory, so the interpreter can only
    target real taxonomy values."""
    def interpret(self, focus: str, categories: Sequence[str]) -> TutorFocus: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _retention(persona_state: dict | None, now: datetime) -> float:
    """Exponential-decay retention estimate in [0, 1]; -1.0 (most due) when the item
    has no usable SRS state (never practiced, or state not yet written). Derived at
    selection time, never stored."""
    if not persona_state:
        return -1.0
    try:
        last_practiced = date.fromisoformat(persona_state[STATE_LAST_PRACTICED_AT])
        half_life_days = float(persona_state[STATE_HALF_LIFE_DAYS])
    except (KeyError, TypeError, ValueError):
        return -1.0
    if half_life_days <= 0:
        return -1.0
    days_since = max((now.date() - last_practiced).days, 0)
    return 2.0 ** (-days_since / half_life_days)


def _curriculum_key(item: MemoryItem) -> tuple[datetime, int]:
    """Cross-type curriculum order: concepts and procedures have independent SERIAL id
    sequences, so ascending id only orders within a type — created_at (monotonic across
    a sequential bundle install) carries the order across types, id breaks ties."""
    return (item.created_at or _EPOCH, item.id or 0)


def _interleave_by_category(items: list[MemoryItem]) -> list[MemoryItem]:
    """Round-robin across categories (first-appearance order), preserving relative
    order within each category — the anti-blocking rule."""
    queues: dict[str | None, list[MemoryItem]] = {}
    for item in items:
        queues.setdefault(item.category, []).append(item)
    result: list[MemoryItem] = []
    pending = list(queues.values())
    while any(pending):
        for queue in pending:
            if queue:
                result.append(queue.pop(0))
    return result


class LanguageTutorSelectionStrategy:
    """Implements PersonaSelectionPort for every language-tutor persona (the strategy
    is language-agnostic: everything pair- or persona-specific comes from the persona's
    own settings and memory rows, never from code)."""

    def __init__(
        self,
        memory_repo: MemoryRepository,
        persona_repo: PersonaRepository,
        embedding_service: EmbeddingService,
        focus_interpreter: FocusInterpreter,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._memory_repo = memory_repo
        self._persona_repo = persona_repo
        self._embedding_service = embedding_service
        self._focus_interpreter = focus_interpreter
        self._now = now_fn

    def select_items(
        self,
        persona_id: UUID,
        focus: str | None = None,
        limit: int = 10,
    ) -> Sequence[SelectedItem]:
        persona = self._persona_repo.get(persona_id)
        settings = (persona.settings if persona else None) or {}
        items = self._memory_repo.list_items(
            persona_id, (MemoryType.CONCEPT, MemoryType.PROCEDURE)
        )
        if not items:
            return []

        tutor_focus = TutorFocus()
        if focus:
            categories = sorted({i.category for i in items if i.category})
            tutor_focus = self._focus_interpreter.interpret(focus, categories)

        if tutor_focus.category:
            filtered = [i for i in items if i.category == tutor_focus.category]
            # A category with no matching items must not zero the session.
            items = filtered or items

        new_pool = sorted(
            (i for i in items if i.engagement_level == EngagementLevel.UNSEEN),
            key=_curriculum_key,
        )
        review_pool = [i for i in items if i.engagement_level > EngagementLevel.UNSEEN]
        if settings.get(SETTING_RANKING) == "retention":
            now = self._now()
            review_pool.sort(key=lambda i: _retention(i.persona_state, now))
        else:
            # Default until retention calibration data exists: least-known first,
            # stalest first within a level.
            review_pool.sort(key=lambda i: (int(i.engagement_level), i.updated_at or _EPOCH))

        batch = self._compose(tutor_focus, new_pool, review_pool, settings, limit)
        batch = _interleave_by_category(batch)
        return self._pair_with_episodes(batch, settings)

    def _compose(
        self,
        tutor_focus: TutorFocus,
        new_pool: list[MemoryItem],
        review_pool: list[MemoryItem],
        settings: dict,
        limit: int,
    ) -> list[MemoryItem]:
        if tutor_focus.topic:
            # Theme-driven session: rank the mode's candidates by similarity to the
            # topic instead of due-ness/curriculum order.
            candidates = {"review": review_pool, "new": new_pool}.get(
                tutor_focus.mode, review_pool + new_pool
            )
            topic_embedding = self._embedding_service.embed(tutor_focus.topic)
            return sorted(
                candidates,
                key=lambda i: _cosine(i.embedding, topic_embedding) if i.embedding else -1.0,
                reverse=True,
            )[:limit]
        if tutor_focus.mode == "review":
            return review_pool[:limit]
        if tutor_focus.mode == "new":
            return new_pool[:limit]
        # Default mixed composition: review share first, new items fill the rest;
        # either pool running short backfills from the other.
        review_share = float(settings.get(SETTING_REVIEW_SHARE, DEFAULT_REVIEW_SHARE))
        n_review = min(len(review_pool), round(limit * review_share))
        n_new = min(len(new_pool), limit - n_review)
        n_review = min(len(review_pool), limit - n_new)
        return review_pool[:n_review] + new_pool[:n_new]

    def _pair_with_episodes(
        self, batch: list[MemoryItem], settings: dict
    ) -> list[SelectedItem]:
        threshold = float(settings.get(SETTING_ANCHOR_THRESHOLD, DEFAULT_ANCHOR_THRESHOLD))
        cap = int(settings.get(SETTING_ELICITATION_CAP, DEFAULT_ELICITATION_CAP))
        hints_used = 0
        selected: list[SelectedItem] = []
        for item in batch:
            context: str | None = None
            if item.embedding:
                matches = self._memory_repo.search(
                    item.embedding, (MemoryType.EPISODE,), top_n=1
                )
                if matches and matches[0][0] >= threshold:
                    context = (
                        "Anchor this item in the user's own experience — a related "
                        f"personal episode: {matches[0][1].summary}"
                    )
                elif hints_used < cap:
                    context = (
                        "No related personal episode is stored yet. If it fits the "
                        "conversation naturally, invite the user to share a short "
                        f"personal story connected to '{item.name}' — the telling "
                        "itself is the practice."
                    )
                    hints_used += 1
            selected.append(SelectedItem(item=item, context=context))
        return selected
