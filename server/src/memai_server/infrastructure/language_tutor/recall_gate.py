# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from ..recall_gate import DefaultRecallGate


class LanguageTutorRecallGate(DefaultRecallGate):
    """A tutor session deliberately makes short replies meaningful — "which word
    would you like to practice?" followed by the learner's single-word answer is
    exactly the content that must be embedded and searched, not skipped as trivial
    (FR-309). Overrides only the length short-circuit; the dedup-against-last-search
    caching behaviour is unchanged, inherited as-is from DefaultRecallGate."""

    def should_embed(self, text: str) -> bool:
        return True
