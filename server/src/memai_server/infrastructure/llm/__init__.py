# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from .ollama import (
    OllamaConsolidationExtractor,
    OllamaDisambiguationEvaluator,
    OllamaLLMService,
    OllamaMemorySynthesizer,
    OllamaRecallIntentDetector,
    OllamaWorthinessEvaluator,
)

# The OpenRouter family is a cloud-gateway *alternative* to the fully-local default —
# re-exported lazily so importing this package (or the Ollama family) never requires the
# `openai` client package at runtime on a fully-local deployment.
_OPENROUTER_EXPORTS = frozenset({
    "OpenRouterLLMService",
    "OpenRouterWorthinessEvaluator",
    "OpenRouterRecallIntentDetector",
    "OpenRouterMemorySynthesizer",
    "OpenRouterDisambiguationEvaluator",
    "OpenRouterConsolidationExtractor",
})


def __getattr__(name: str):
    if name in _OPENROUTER_EXPORTS:
        from . import openrouter
        return getattr(openrouter, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "OllamaLLMService",
    "OllamaWorthinessEvaluator",
    "OllamaRecallIntentDetector",
    "OllamaMemorySynthesizer",
    "OllamaDisambiguationEvaluator",
    "OllamaConsolidationExtractor",
    *sorted(_OPENROUTER_EXPORTS),
]
