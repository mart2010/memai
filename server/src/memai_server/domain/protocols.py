# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from typing import Protocol

from .events import RecallTriggered


class RecallIntentDetector(Protocol):
    """Detects explicit recall intent in user speech and extracts a structured query."""

    def detect(self, text: str) -> RecallTriggered | None: ...


