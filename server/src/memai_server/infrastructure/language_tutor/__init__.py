# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Language-tutor persona class ("language_tutor" strategy set).

Everything tutor-specific lives in this package — category taxonomy, focus
interpretation, SRS persona_state vocabulary. Generic code never imports it;
the composition root (server.py) binds it to personas whose
`AssistantPersona.strategy` declares STRATEGY_NAME.
"""
from .selection import (
    STRATEGY_NAME,
    FocusInterpreter,
    LanguageTutorSelectionStrategy,
    TutorFocus,
)
from .assessment import LanguageTutorAssessmentStrategy, PracticeJudge, PracticeJudgment
from .enrichment import ClusterProposer, LanguageTutorEnrichmentStrategy, ProposedItem
from .cluster_ollama import OllamaClusterProposer
from .focus_ollama import OllamaFocusInterpreter
from .judge_ollama import OllamaPracticeJudge
from .recall_gate import LanguageTutorRecallGate

__all__ = [
    "STRATEGY_NAME",
    "ClusterProposer",
    "FocusInterpreter",
    "LanguageTutorAssessmentStrategy",
    "LanguageTutorEnrichmentStrategy",
    "LanguageTutorRecallGate",
    "LanguageTutorSelectionStrategy",
    "OllamaClusterProposer",
    "OllamaFocusInterpreter",
    "OllamaPracticeJudge",
    "PracticeJudge",
    "PracticeJudgment",
    "ProposedItem",
    "TutorFocus",
]
