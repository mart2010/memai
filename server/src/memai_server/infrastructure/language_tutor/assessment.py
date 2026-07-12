# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Language-tutor assessment strategy — the first concrete PersonaAssessmentPort.

Runs OFFLINE in the consolidation pipeline after upsert. For each touched item it
merges fresh conversational evidence (an LLM practice judgment + turn-timestamp
latency) into the item's SRS persona_state. Writes from day one, even while selection
still ranks by engagement_level — the instrument-now-calibrate-later posture.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from ...domain.model import Concept, Conversation, Episode, MemoryType, Speaker
from ...services.ports import (
    ItemAssessment,
    MemoryItem,
    MemoryRepository,
    PersonaRepository,
    UserRepository,
)
from .state import (
    STATE_AVG_RESPONSE_LATENCY_S,
    STATE_ERRORS,
    STATE_HALF_LIFE_DAYS,
    STATE_LAST_PRACTICED_AT,
    STATE_RETRIEVALS,
    STATE_SESSIONS_PRACTICED,
    STATE_USER_INITIATED,
)

# AssistantPersona.settings keys with defaults — placeholders pending half-life
# calibration (PLAN.md Phase 12, same posture as the upsert thresholds).
SETTING_INITIAL_HALF_LIFE = "initial_half_life_days"
SETTING_HALF_LIFE_GROWTH = "half_life_growth"
SETTING_HALF_LIFE_SHRINK = "half_life_shrink"
SETTING_USER_INITIATED_BOOST = "user_initiated_boost"
SETTING_PAIR_DIFFICULTY = "pair_difficulty"  # map keyed by LEARNER language, "*" fallback

DEFAULT_INITIAL_HALF_LIFE = 1.0
DEFAULT_HALF_LIFE_GROWTH = 2.0
DEFAULT_HALF_LIFE_SHRINK = 0.5
DEFAULT_USER_INITIATED_BOOST = 2.0
MIN_HALF_LIFE_DAYS = 0.5


@dataclass(frozen=True)
class PracticeJudgment:
    """LLM verdict on how one item was practised within a conversation, matched back
    to the item by name. `retrievals` counts SUCCESSFUL retrievals only (the
    successive-relearning rule) — mere exposure is not a retrieval."""
    name: str
    retrievals: int = 0
    errors: int = 0
    user_initiated: bool = False


_NO_PRACTICE = PracticeJudgment(name="")  # exposure only — still updates the day anchor


class PracticeJudge(Protocol):
    def judge(
        self, conversation: Conversation, items: Sequence[MemoryItem]
    ) -> Sequence[PracticeJudgment]: ...


def _average_response_latency(conversation: Conversation) -> float | None:
    """Mean user-response latency over the conversation: user turn timestamp minus the
    preceding assistant turn's (which is stamped when the LLM finished — the closest
    stored proxy for end-of-TTS→speech-start). Turn-level and noisy, hence weighted low
    downstream; per-item attribution is deliberately not attempted."""
    deltas: list[float] = []
    previous = None
    for turn in conversation.turns:
        if (
            previous is not None
            and previous.speaker == Speaker.ASSISTANT
            and turn.speaker == Speaker.USER
        ):
            delta = (turn.timestamp - previous.timestamp).total_seconds()
            if delta >= 0:
                deltas.append(delta)
        previous = turn
    return sum(deltas) / len(deltas) if deltas else None


class LanguageTutorAssessmentStrategy:
    """Implements PersonaAssessmentPort for every language-tutor persona."""

    def __init__(
        self,
        memory_repo: MemoryRepository,
        persona_repo: PersonaRepository,
        user_repo: UserRepository,
        judge: PracticeJudge,
    ) -> None:
        self._memory_repo = memory_repo
        self._persona_repo = persona_repo
        self._user_repo = user_repo
        self._judge = judge

    def assess_items(
        self,
        persona_id: UUID,
        conversation: Conversation,
        touched_items: Sequence[MemoryItem],
    ) -> Sequence[ItemAssessment]:
        items = [i for i in touched_items if i.id is not None and not self._is_episode(i)]
        if not items:
            return []

        settings = self._settings(persona_id)
        difficulty = self._pair_difficulty(settings)
        # Touched items come from extraction — their persona_state attribute is None
        # even when the row already carries state (upserts structurally exclude the
        # column), so the CURRENT stored state is read back from the repository.
        stored_state = self._stored_states(persona_id)
        judgments = {j.name: j for j in self._judge.judge(conversation, items)}
        practiced_on = (conversation.ended_at or conversation.started_at).date()
        latency = _average_response_latency(conversation)

        assessments: list[ItemAssessment] = []
        for item in items:
            key = (self._memory_type(item), item.id)
            state = self._update_state(
                existing=stored_state.get(key),
                # Unjudged item (LLM omitted it): exposure only — the day anchor and
                # session count still move, retrievals/errors don't.
                judgment=judgments.get(item.name, _NO_PRACTICE),
                practiced_on=practiced_on,
                latency=latency,
                settings=settings,
                difficulty=difficulty,
            )
            assessments.append(
                ItemAssessment(item_id=item.id, memory_type=key[0], persona_state=state)
            )
        return assessments

    def _update_state(
        self,
        existing: dict | None,
        judgment: PracticeJudgment,
        practiced_on: date,
        latency: float | None,
        settings: dict,
        difficulty: float,
    ) -> dict:
        growth = float(settings.get(SETTING_HALF_LIFE_GROWTH, DEFAULT_HALF_LIFE_GROWTH))
        shrink = float(settings.get(SETTING_HALF_LIFE_SHRINK, DEFAULT_HALF_LIFE_SHRINK))

        if existing is None:
            initial = float(settings.get(SETTING_INITIAL_HALF_LIFE, DEFAULT_INITIAL_HALF_LIFE))
            if judgment.user_initiated:
                initial *= float(
                    settings.get(SETTING_USER_INITIATED_BOOST, DEFAULT_USER_INITIATED_BOOST)
                )
            half_life = initial / difficulty
            sessions = 0
            retrievals = errors = 0
            user_initiated = judgment.user_initiated
            avg_latency = None
            new_day = True
        else:
            half_life = float(existing.get(STATE_HALF_LIFE_DAYS, DEFAULT_INITIAL_HALF_LIFE))
            sessions = int(existing.get(STATE_SESSIONS_PRACTICED, 0))
            retrievals = int(existing.get(STATE_RETRIEVALS, 0))
            errors = int(existing.get(STATE_ERRORS, 0))
            user_initiated = bool(existing.get(STATE_USER_INITIATED, False)) or judgment.user_initiated
            avg_latency = existing.get(STATE_AVG_RESPONSE_LATENCY_S)
            try:
                last = date.fromisoformat(existing.get(STATE_LAST_PRACTICED_AT, ""))
                new_day = practiced_on > last
            except ValueError:
                new_day = True

        # Sleep-gated spacing: half-life only moves on a NEW day. Errors shrink it
        # regardless of successes in the same conversation (errors are the stronger
        # signal); an error-free conversation with at least one successful retrieval
        # grows it. Same-day repetition adjusts counts only.
        if new_day and existing is not None:
            if judgment.errors > 0:
                half_life = max(half_life * shrink, MIN_HALF_LIFE_DAYS)
            elif judgment.retrievals > 0:
                half_life *= growth

        if latency is not None:
            avg_latency = (
                latency
                if avg_latency is None
                else (float(avg_latency) * sessions + latency) / (sessions + 1)
            )

        return {
            STATE_LAST_PRACTICED_AT: practiced_on.isoformat(),
            STATE_HALF_LIFE_DAYS: half_life,
            STATE_RETRIEVALS: retrievals + judgment.retrievals,
            STATE_ERRORS: errors + judgment.errors,
            STATE_AVG_RESPONSE_LATENCY_S: avg_latency,
            STATE_USER_INITIATED: user_initiated,
            STATE_SESSIONS_PRACTICED: sessions + 1,
        }

    def _settings(self, persona_id: UUID) -> dict:
        persona = self._persona_repo.get(persona_id)
        return (persona.settings if persona else None) or {}

    def _pair_difficulty(self, settings: dict) -> float:
        """Resolved against the learner's language — the map keeps main bundles
        pair-independent (docs/BRIEF_phase11_bundle_format.md)."""
        difficulty_map = settings.get(SETTING_PAIR_DIFFICULTY)
        if not isinstance(difficulty_map, dict):
            return 1.0
        user = self._user_repo.get()
        code = user.primary_language.code if user and user.primary_language else None
        value = difficulty_map.get(code, difficulty_map.get("*", 1.0))
        try:
            return float(value) or 1.0
        except (TypeError, ValueError):
            return 1.0

    def _stored_states(self, persona_id: UUID) -> dict[tuple[MemoryType, int], dict]:
        items = self._memory_repo.list_items(
            persona_id, (MemoryType.CONCEPT, MemoryType.PROCEDURE)
        )
        return {
            (self._memory_type(i), i.id): i.persona_state
            for i in items
            if i.id is not None and i.persona_state
        }

    @staticmethod
    def _memory_type(item: MemoryItem) -> MemoryType:
        return MemoryType.CONCEPT if isinstance(item, Concept) else MemoryType.PROCEDURE

    @staticmethod
    def _is_episode(item: MemoryItem) -> bool:
        return isinstance(item, Episode)
