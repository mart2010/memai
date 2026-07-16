# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from .ollama import (
    OllamaConsolidationExtractor,
    OllamaDisambiguationEvaluator,
    OllamaLLMService,
    OllamaMemorySynthesizer,
    OllamaWorthinessEvaluator,
)

# The OpenRouter family (offline evaluators only — see openrouter.py) is a cloud-gateway
# *alternative* to the fully-local default, not wired into the composition root yet
# (TR-953). OpenAICompatibleLLMService (live conversation only: FR-707/TR-955) *is*
# wired in, conditionally, by server.py. Both re-exported lazily so importing this
# package (or the Ollama family) never requires the `openai` client package at runtime
# on a fully-local deployment.
_OPENROUTER_EXPORTS = frozenset({
    "OpenRouterWorthinessEvaluator",
    "OpenRouterMemorySynthesizer",
    "OpenRouterDisambiguationEvaluator",
    "OpenRouterConsolidationExtractor",
})
_OPENAI_COMPATIBLE_EXPORTS = frozenset({
    "OpenAICompatibleLLMService",
})


def __getattr__(name: str):
    if name in _OPENROUTER_EXPORTS:
        from . import openrouter
        return getattr(openrouter, name)
    if name in _OPENAI_COMPATIBLE_EXPORTS:
        from . import openai_compatible
        return getattr(openai_compatible, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "OllamaLLMService",
    "OllamaWorthinessEvaluator",
    "OllamaMemorySynthesizer",
    "OllamaDisambiguationEvaluator",
    "OllamaConsolidationExtractor",
    *sorted(_OPENROUTER_EXPORTS),
    *sorted(_OPENAI_COMPATIBLE_EXPORTS),
]
