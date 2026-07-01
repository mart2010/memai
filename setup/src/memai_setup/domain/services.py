# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from .model import FitAssessment, FitLevel, VRAMEstimate

# VRAM reserved for STT+TTS when picking the LLM (see docs/PLAN.md Phase 4
# findings: llama3.3 70B starved this headroom and got split CPU/GPU by
# Ollama, causing multi-second cold-reload stalls) and reserved for TTS alone
# when picking the Whisper model size, since the LLM's own footprint is
# already known exactly at that point (plan.llm_model_id) and added on top —
# see ResolveSTTEngine.
LLM_SELECTION_HEADROOM_GB = 3.0
STT_SELECTION_TTS_HEADROOM_GB = 2.0


def assess_fit(vram: VRAMEstimate, available_vram_gb: float | None, reserved_gb: float) -> FitAssessment:
    """Pure domain service — no infrastructure dependency. `available_vram_gb`
    is `None` when GPUDetector could not read the GPU; treated as TIGHT with a
    warning rather than blocking the choice outright. `reserved_gb` is
    whatever else needs to run alongside this component (see the two module
    constants above) — kept explicit rather than hardcoded so this function
    works for both LLM and Whisper-model-size fit checks."""
    if available_vram_gb is None:
        return FitAssessment(
            level=FitLevel.TIGHT,
            message="GPU VRAM could not be detected — proceed with caution.",
        )

    headroom_gb = available_vram_gb - reserved_gb
    if headroom_gb >= vram.recommended_gb:
        return FitAssessment(
            level=FitLevel.COMFORTABLE,
            message=f"Fits comfortably in {available_vram_gb:.0f} GB VRAM.",
        )
    if headroom_gb >= vram.min_gb:
        return FitAssessment(
            level=FitLevel.TIGHT,
            message="Fits, but tightly — may be slow to reload after idle eviction.",
        )
    return FitAssessment(
        level=FitLevel.WONT_FIT,
        message=f"Does not fit — needs at least {vram.min_gb:.0f} GB free alongside the rest of the pipeline.",
    )
