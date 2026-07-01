# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from typing import Protocol

from ..domain.model import FitLevel
from ..domain.plan import InstallationPlan, Topology
from ..domain.services import assess_fit
from .ports import (
    CatalogueRepository,
    ConfigWriter,
    GPUDetector,
    ModelInstaller,
    PromptChoice,
    SchemaRunner,
    WizardPrompter,
)


class WizardStep(Protocol):
    """One page of the wizard flow. Reads/mutates the shared InstallationPlan
    and talks to the user only through WizardPrompter — this keeps every step
    unit-testable with Fakes, no real terminal or subprocess involved."""

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None: ...


class SelectTopology:
    """Flow step 2. No-op if a previous install already locked the topology."""

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        if plan.topology is not None:
            return
        choice = prompter.select(
            "How is Mémai deployed?",
            [
                PromptChoice("single_host", "Single machine (client + server together)"),
                PromptChoice("split_host", "Split (server on GPU machine, client elsewhere)"),
            ],
        )
        plan.set_topology(Topology.SINGLE_HOST if choice == "single_host" else Topology.SPLIT_HOST)


class SelectLLM:
    """Flow steps 4-5. Presents every catalogue entry with a plain-English fit
    hint — never filters the list, per CLAUDE.md ("user always sees all
    options, never a filtered list")."""

    def __init__(self, catalogues: CatalogueRepository, gpu: GPUDetector) -> None:
        self._catalogues = catalogues
        self._gpu = gpu

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        vram_gb = self._gpu.detect_vram_gb()
        if vram_gb is None:
            prompter.info("Could not detect GPU VRAM — fit hints below are best-effort.")

        entries = self._catalogues.load_llm_catalogue()
        choices = []
        for entry in entries:
            fit = assess_fit(entry, vram_gb)
            warning = "" if fit.level != FitLevel.WONT_FIT else " ⚠"
            recommended = " (recommended)" if entry.recommended else ""
            # Enforced structurally, not left to catalogue description prose —
            # see the `reasoning` field comment in domain/model.py.
            reasoning_warning = " ⚠ reasoning model — <think> block is spoken aloud" if entry.reasoning else ""
            choices.append(
                PromptChoice(
                    entry.model_id,
                    f"{entry.display_name}{recommended} — {fit.message}{warning}{reasoning_warning}",
                )
            )

        plan.llm_model_id = prompter.select("Choose a language model:", choices)


class SelectLanguages:
    """Flow step 6. TODO: derive the offered list from the intersection of
    STT/TTS catalogue coverage; each selection drives ResolveSTTEngine /
    ResolveTTSEngines below."""

    def __init__(self, catalogues: CatalogueRepository) -> None:
        self._catalogues = catalogues

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        raise NotImplementedError("TODO: language selection from catalogue-derived coverage")


class ResolveSTTEngine:
    """Flow step 7. TODO: faster-whisper covers ~99 languages unconditionally
    today — mainly a Whisper model-size choice (VRAM tradeoff), not an engine
    choice. Revisit if a second STT engine is ever added."""

    def __init__(self, catalogues: CatalogueRepository, installer: ModelInstaller) -> None:
        self._catalogues = catalogues
        self._installer = installer

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        raise NotImplementedError("TODO: Whisper model-size selection + download")


class ResolveTTSEngines:
    """Flow step 8. TODO: per selected language, if only one engine covers it,
    install that one; if multiple engines cover it (e.g. both Kokoro and Piper
    offer English), let the user pick rather than silently defaulting to one —
    voice variety/quality is a stated goal, not just coverage. Download only
    what's missing (additive, never removed). Coqui/XTTS stays out for now
    (licence conflict) but isn't ruled out long-term — see
    project_tts_license_conflict memory and catalogues/tts_catalogue.toml."""

    def __init__(self, catalogues: CatalogueRepository, installer: ModelInstaller) -> None:
        self._catalogues = catalogues
        self._installer = installer

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        raise NotImplementedError("TODO: per-language TTS engine resolution + voice download")


class GenerateConfig:
    """Flow step 9. TODO: write via ConfigWriter port to platformdirs.user_config_dir("memai")."""

    def __init__(self, writer: ConfigWriter) -> None:
        self._writer = writer

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        raise NotImplementedError("TODO: translate InstallationPlan into server/client TOML config")


class SetupSchema:
    """Flow step 10. TODO: idempotent apply of migrations/001_initial_schema.sql."""

    def __init__(self, schema_runner: SchemaRunner) -> None:
        self._schema_runner = schema_runner

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        raise NotImplementedError("TODO: idempotent schema apply")


class RunHealthChecks:
    """Flow step 11. TODO: Postgres reachable, Ollama running, Whisper loads,
    TTS loads, server WebSocket answers — run server as a subprocess to verify."""

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        raise NotImplementedError("TODO: end-to-end health checks")
