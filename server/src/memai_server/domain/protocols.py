# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from typing import Protocol

from .events import RecallTriggered
from .model import Conversation


class RecallIntentDetector(Protocol):
    """Detects explicit recall intent in user speech and extracts a structured query."""

    def detect(self, text: str) -> RecallTriggered | None: ...


class WorthinessEvaluator(Protocol):
    """Judges whether a Conversation is substantial enough to extract an Episode from
    during consolidation — a domain-level judgment call, not an infrastructure concern."""

    def evaluate(self, conversation: Conversation) -> bool: ...


