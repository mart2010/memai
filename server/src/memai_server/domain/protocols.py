# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from typing import Protocol

from .events import RecallTriggered
from .model import Conversation


class WorthinessEvaluator(Protocol):
    """Decides whether a Conversation is worth persisting as an Episode."""

    def evaluate(self, conversation: Conversation) -> bool: ...


class RecallIntentDetector(Protocol):
    """Detects explicit recall intent in user speech and extracts a structured query."""

    def detect(self, text: str) -> RecallTriggered | None: ...


class PersonaIntentDetector(Protocol):
    """Detects explicit persona switch intent via LLM self-reporting ([PERSONA:name] prefix)."""

    def detect(self, text: str) -> str | None: ...
