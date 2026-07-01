# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

# ISO 639-1 code. `setup` is an independent package (own venv, no cross-package
# import) — coverage is validated against the catalogues themselves, not a
# shared Language value object from memai_server.
LanguageCode = str


@dataclass(frozen=True)
class VRAMEstimate:
    min_gb: float
    recommended_gb: float


class FitLevel(Enum):
    COMFORTABLE = auto()
    TIGHT = auto()
    WONT_FIT = auto()


@dataclass(frozen=True)
class FitAssessment:
    level: FitLevel
    message: str


@dataclass(frozen=True)
class LLMCatalogueEntry:
    model_id: str  # ollama pull target, e.g. "aya-expanse"
    display_name: str
    vram: VRAMEstimate
    languages: frozenset[LanguageCode]  # {"*"} = effectively unrestricted (e.g. Gemma 3's 140+ languages)
    recommended: bool
    # Reasoning-tuned models emit a <think>...</think> block that gets spoken
    # aloud by TTS (think:false does not suppress it — see project_known_issues
    # memory / docs/PLAN.md Phase 4 findings). A real selection criterion, not
    # just prose — SelectLLM enforces a warning on every reasoning=true entry.
    reasoning: bool
    description: str


@dataclass(frozen=True)
class WhisperModelEntry:
    name: str  # faster-whisper model size, e.g. "small", "medium"
    vram: VRAMEstimate


@dataclass(frozen=True)
class STTCatalogueEntry:
    engine: str  # e.g. "faster-whisper"
    models: tuple[WhisperModelEntry, ...]
    languages: frozenset[LanguageCode]  # {"*"} = effectively unrestricted
    # False = catalogued as a real option but no infrastructure adapter exists
    # yet (e.g. whisper.cpp) — ResolveSTTEngine must not let a user install an
    # engine it can't actually wire up. Same "make it explicit, not prose"
    # rationale as LLMCatalogueEntry.reasoning.
    has_adapter: bool
    description: str


@dataclass(frozen=True)
class TTSVoiceEntry:
    voice_id: str
    language: LanguageCode
    display_name: str


@dataclass(frozen=True)
class TTSCatalogueEntry:
    engine: str  # "kokoro" | "piper"
    licence: str
    languages: frozenset[LanguageCode]
    voices: tuple[TTSVoiceEntry, ...]
    description: str
