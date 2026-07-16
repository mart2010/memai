# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.model import DetectedGPU, LLMCatalogueEntry, STTCatalogueEntry, TTSCatalogueEntry
from ..domain.plan import InstallationPlan


@dataclass(frozen=True)
class PromptChoice:
    value: str
    label: str
    # Pre-selected in a select_many checkbox — how re-runs show what's already
    # installed (FR-706). Ignored by single-choice select().
    checked: bool = False


class WizardPrompter(Protocol):
    """Terminal interaction port. QuestionaryPrompter implements it for real
    runs; FakeWizardPrompter replays scripted answers in unit tests — no
    unittest.mock, per CLAUDE.md testing conventions."""

    def select(self, message: str, choices: list[PromptChoice], default: str | None = None) -> str:
        """`default` is the value to pre-highlight (e.g. the currently installed
        LLM on a re-run); a value not present among `choices` is ignored."""
        ...

    def select_many(self, message: str, choices: list[PromptChoice]) -> list[str]: ...
    def confirm(self, message: str, default: bool = True) -> bool: ...
    def text(self, message: str, default: str = "") -> str: ...
    def info(self, message: str) -> None: ...

    def heading(self, title: str, lines: list[str] | None = None) -> None:
        """A visually distinct section banner (e.g. the welcome screen) —
        deliberately separate from `info()` so it can't be confused with a
        routine status line while scanning the terminal."""
        ...


class CatalogueRepository(Protocol):
    def load_llm_catalogue(self) -> tuple[LLMCatalogueEntry, ...]: ...
    def load_stt_catalogue(self) -> tuple[STTCatalogueEntry, ...]: ...
    def load_tts_catalogue(self) -> tuple[TTSCatalogueEntry, ...]: ...


class GPUDetector(Protocol):
    def detect_vram_gb(self) -> float | None: ...  # None = undetectable; NVIDIA/CUDA only

    def detect_gpu(self) -> DetectedGPU | None:
        """Best-effort identification of any GPU, called as a fallback only
        when detect_vram_gb() found nothing — so callers can tell "no GPU at
        all" apart from "a real GPU is here, just not one this sizing check
        recognizes" (e.g. AMD). Returns None if nothing could be identified
        either."""
        ...


class ExistingInstallDetector(Protocol):
    """Reads a previously-written config (if any) and pre-fills a plan for
    re-runs. Returns None on a fresh install."""

    def load_existing_plan(self) -> InstallationPlan | None: ...


class ModelInstaller(Protocol):
    """Engines are additive — never removed (CLAUDE.md). Each method is
    idempotent: safe to call again if a prior run was interrupted."""

    def pull_llm(self, model_id: str) -> None: ...
    def download_whisper_model(self, name: str) -> None: ...
    def download_piper_voice(self, voice_id: str) -> None: ...
    def download_embedding_model(self) -> None: ...


class ConfigWriter(Protocol):
    def write_server_config(self, plan: InstallationPlan) -> None: ...
    def write_client_config(self, plan: InstallationPlan) -> None: ...


class SchemaRunner(Protocol):
    def apply_schema(self, database_url: str) -> None: ...  # idempotent, no migration framework


@dataclass(frozen=True)
class HealthCheckResult:
    name: str
    ok: bool
    message: str


class HealthCheck(Protocol):
    name: str

    def check(self) -> HealthCheckResult: ...


class DatabaseConnectionVerifier(Protocol):
    """Verifies a candidate database_url actually works, used by
    ConfigureDatabaseConnection while it's still deciding on/confirming a
    connection string — distinct from HealthCheck, which re-verifies an
    already-fixed URL later (RunHealthChecks)."""

    def verify(self, database_url: str) -> tuple[HealthCheckResult, HealthCheckResult]:
        """Returns (postgres_reachable, pgvector_installed)."""
        ...
