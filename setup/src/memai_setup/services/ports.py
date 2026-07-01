# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.model import LLMCatalogueEntry, STTCatalogueEntry, TTSCatalogueEntry
from ..domain.plan import InstallationPlan


@dataclass(frozen=True)
class PromptChoice:
    value: str
    label: str


class WizardPrompter(Protocol):
    """Terminal interaction port. QuestionaryPrompter implements it for real
    runs; FakeWizardPrompter replays scripted answers in unit tests — no
    unittest.mock, per CLAUDE.md testing conventions."""

    def select(self, message: str, choices: list[PromptChoice]) -> str: ...
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
    def detect_vram_gb(self) -> float | None: ...  # None = undetectable


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
