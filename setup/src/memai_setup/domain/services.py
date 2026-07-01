# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from .model import FitAssessment, FitLevel, LLMCatalogueEntry

# VRAM reserved for Whisper + Kokoro running alongside the LLM. See
# docs/PLAN.md Phase 4 findings: llama3.3 (70B) starved this headroom and got
# split CPU/GPU by Ollama, causing multi-second cold-reload stalls.
_COMPANION_HEADROOM_GB = 3.0


def assess_fit(entry: LLMCatalogueEntry, available_vram_gb: float | None) -> FitAssessment:
    """Pure domain service — no infrastructure dependency. `available_vram_gb`
    is `None` when GPUDetector could not read the GPU; treated as TIGHT with a
    warning rather than blocking the choice outright."""
    if available_vram_gb is None:
        return FitAssessment(
            level=FitLevel.TIGHT,
            message="GPU VRAM could not be detected — proceed with caution.",
        )

    headroom_gb = available_vram_gb - _COMPANION_HEADROOM_GB
    if headroom_gb >= entry.vram.recommended_gb:
        return FitAssessment(
            level=FitLevel.COMFORTABLE,
            message=f"Fits comfortably in {available_vram_gb:.0f} GB VRAM.",
        )
    if headroom_gb >= entry.vram.min_gb:
        return FitAssessment(
            level=FitLevel.TIGHT,
            message="Fits, but tightly — may be slow to reload after idle eviction.",
        )
    return FitAssessment(
        level=FitLevel.WONT_FIT,
        message=f"Does not fit — needs at least {entry.vram.min_gb:.0f} GB free after Whisper/Kokoro.",
    )
