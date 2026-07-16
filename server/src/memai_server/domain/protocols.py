# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from typing import Protocol

from .model import Conversation


class WorthinessEvaluator(Protocol):
    """Judges whether a Conversation is substantial enough to extract an Episode from
    during consolidation — a domain-level judgment call, not an infrastructure concern."""

    def evaluate(self, conversation: Conversation) -> bool: ...


