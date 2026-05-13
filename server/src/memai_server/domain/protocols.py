from typing import Protocol

from .events import RecallTriggered
from .model import ConversationRecord


class WorthinessEvaluator(Protocol):
    """Decides whether a ConversationRecord is worth persisting as an Episode."""

    def evaluate(self, record: ConversationRecord) -> bool: ...


class RecallIntentDetector(Protocol):
    """Detects explicit recall intent in user speech and extracts a structured query."""

    def detect(self, text: str) -> RecallTriggered | None: ...


class PersonaIntentDetector(Protocol):
    """Detects explicit persona switch intent via LLM self-reporting ([PERSONA:name] prefix)."""

    def detect(self, text: str) -> str | None: ...
