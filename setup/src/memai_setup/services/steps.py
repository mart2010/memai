# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import getpass
import subprocess
from typing import Protocol

from ..domain.language_coverage import offered_languages
from ..domain.languages import format_language
from ..domain.model import FitLevel
from ..domain.plan import InstallationPlan, Topology
from ..domain.services import LLM_SELECTION_HEADROOM_GB, STT_SELECTION_TTS_HEADROOM_GB, assess_fit
from .errors import WizardAborted
from .ports import (
    CatalogueRepository,
    ConfigWriter,
    DatabaseConnectionVerifier,
    GPUDetector,
    HealthCheck,
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


class ShowWelcome:
    """Flow step 1. Purely informational — rendered as one `heading()` banner
    (not a run of `info()` lines) so it reads as a distinct section rather
    than blending into the routine status lines later steps print. Briefly
    explains single-host vs. split-host up front since the SSH prerequisite
    below only makes sense once that distinction exists — SelectTopology
    (step 2) is where the user actually picks one. Lists every prerequisite,
    including the ones nothing in this wizard can verify programmatically
    (GPU driver, PortAudio, SSH key auth); CheckPrerequisites (step 3)
    verifies the subset that's actually checkable (Postgres, pgvector,
    Ollama).

    The GPU bullets deliberately don't overstate the no-GPU case: STT/TTS on
    CPU is a validated fast path (benchmarked on a Strix Halo APU — several
    times faster than realtime once warm, see DetectComputeDevice), but LLM
    speed on CPU-only is Ollama's own concern, not something this codebase
    controls or has characterized, and there is no cloud/alternative LLM
    backend to fall back to (single-user, local-only is a hard project
    constraint — see CLAUDE.md)."""

    _PREREQUISITES = (
        "PostgreSQL 15+ with the pgvector extension installed (not just Postgres running), "
        "and a 'memai' role/database created — see docs/INSTALL_SERVER.md. The next step "
        "prefers passwordless peer auth (Linux/macOS): if your OS user isn't yet mapped to "
        "the 'memai' role in pg_ident.conf, that step will show you the exact lines to add",
        "Ollama installed and running",
        "(Optional) A GPU speeds things up. NVIDIA (CUDA 12 + cuDNN 9) accelerates "
        "STT, TTS, and the LLM. AMD GPUs are auto-detected by Ollama and accelerate "
        "the LLM only — STT/TTS have no AMD-accelerated path yet and always run on CPU",
        "With no GPU at all, everything still works: STT/TTS run comfortably on CPU "
        "(benchmarked fast on modern hardware), but LLM responses run via Ollama on CPU "
        "too and will be noticeably slower depending on model size — there is currently "
        "no cloud or alternative LLM service configured as a fallback",
        "SSH server + key auth on the server machine — only if you'll use split-host "
        "(see below); not needed for single-host",
        "PortAudio — macOS/Linux client only (`brew install portaudio` / `apt install "
        "libportaudio2`); Windows wheels already bundle it, nothing to install",
    )

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        prompter.heading(
            "Welcome to the Mémai installation wizard",
            [
                "Mémai can be installed two ways — you'll choose one in the next step:",
                "  - Single-host: client and server run on this same machine.",
                "  - Split-host: the server runs on a separate GPU machine; this client",
                "    connects to it over an SSH tunnel.",
                "",
                "Before continuing, make sure you have:",
                *[f"  - {item}" for item in self._PREREQUISITES],
                "",
                "The next steps will verify Postgres and Ollama automatically where possible.",
            ],
        )


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


class ConfigureDatabaseConnection:
    """Flow step 3. Collects and verifies a real Postgres connection —
    fills a long-documented gap where plan.database_url was always the class
    default (postgresql://memai:changeme@...), decoupled from whatever the
    wizard's health checks/SetupSchema actually needed (see docs/PLAN.md
    "Known gaps"). Never creates the role/database itself — Postgres+pgvector
    installation and the memai role/database are documented manual
    prerequisites (see ShowWelcome), same as today. Safe to re-run: this step
    only collects+verifies, so a pre-existing memai role/database (from a
    prior wizard run) is the normal case, not a special one.

    Peer auth (Unix socket, no password) is the default/recommended path on
    Linux/macOS — psycopg/libpq treats an empty host as "use the default
    local socket", so the DSN only needs an explicit `user=` (the fixed
    "memai" role) to route around peer auth's default "OS username == role
    name" behavior; that requires a one-time pg_ident.conf mapping this step
    documents on failure. Falls back to host+password for remote/non-standard
    setups. Windows has no `peer` auth at all (its equivalent, `sspi`, is a
    documented future follow-up) — this step's peer-auth path is Linux/macOS
    only, matching where `server/` currently runs (CLAUDE.md)."""

    _PEER_AUTH_URL = "postgresql:///memai?user=memai"

    def __init__(self, verifier: DatabaseConnectionVerifier) -> None:
        self._verifier = verifier

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        choice = prompter.select(
            "How should Mémai connect to PostgreSQL?",
            [
                PromptChoice("peer", "Local peer authentication (recommended) — no password stored"),
                PromptChoice("password", "Host + password (remote or custom Postgres setup)"),
            ],
        )
        if choice == "peer":
            database_url = self._PEER_AUTH_URL
            failure_hint = self._peer_auth_hint()
        else:
            host = prompter.text("Postgres host:", default="localhost")
            port = prompter.text("Postgres port:", default="5432")
            dbname = prompter.text("Database name:", default="memai")
            user = prompter.text("Database user:", default="memai")
            password = prompter.text("Database password:", default="")
            database_url = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
            failure_hint = ""

        postgres_result, pgvector_result = self._verifier.verify(database_url)
        if not postgres_result.ok:
            proceed = prompter.confirm(
                f"Could not connect to Postgres ({postgres_result.message}).{failure_hint} "
                "Continue anyway (fix it later and re-run memai-setup)?",
                default=False,
            )
            if not proceed:
                raise WizardAborted("Installation cancelled — fix the Postgres connection and re-run memai-setup.")
        elif not pgvector_result.ok:
            prompter.info(f"pgvector: {pgvector_result.message}")

        plan.database_url = database_url

    def _peer_auth_hint(self) -> str:
        os_user = getpass.getuser()
        return (
            " This usually means the 'memai' role isn't mapped to your OS user "
            f"('{os_user}') yet — add to pg_ident.conf:\n"
            f"  memai_map    {os_user}    memai\n"
            "and to pg_hba.conf (before the general 'local all all peer' line):\n"
            "  local   memai   memai   peer map=memai_map\n"
            "then reload Postgres (`sudo systemctl reload postgresql`) and re-run memai-setup."
        )


class CheckPrerequisites:
    """Flow step 4. Warn-and-confirm, not hard-block: reports every check's
    result, and if anything failed, asks whether to continue anyway (e.g. the
    user knows Ollama will be up by the time SelectLLM needs to pull a model)
    rather than refusing outright. Raises WizardAborted if the user declines
    — caught at the CLI boundary for a clean exit instead of a raw traceback.
    Postgres/pgvector are checked separately by ConfigureDatabaseConnection
    (step 3, just before this one), which needs the connection details it
    just collected — not a fixed check this step can hold up front."""

    def __init__(self, checks: list[HealthCheck]) -> None:
        self._checks = checks

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        prompter.info("Checking prerequisites...")
        failed_names = []
        for check in self._checks:
            result = check.check()
            status = "OK" if result.ok else "FAILED"
            prompter.info(f"[{status}] {check.name}: {result.message}")
            if not result.ok:
                failed_names.append(check.name)

        if not failed_names:
            return
        proceed = prompter.confirm(
            f"{len(failed_names)} prerequisite check(s) failed ({', '.join(failed_names)}). Continue anyway?",
            default=False,
        )
        if not proceed:
            raise WizardAborted("Installation cancelled — fix the prerequisites above and re-run memai-setup.")


class DetectComputeDevice:
    """Flow step 5. Sets plan.compute_device — the single source of truth
    GenerateConfig/TomlConfigWriter reads to write [stt].device/compute_type
    and [tts].device. Distinct from SelectLLM's and ResolveSTTEngine's own
    gpu.detect_vram_gb() calls, which are about VRAM-amount fit hints for a
    given model choice, not this CUDA-presence fact — cheap enough to call
    nvidia-smi more than once rather than thread this step's result through
    every later step's fit-hint logic.

    NvidiaSmiGPUDetector only ever recognizes NVIDIA/CUDA (see
    infrastructure/gpu.py), so None here covers both "no GPU" and "GPU
    present but not NVIDIA" (e.g. AMD) — ROCm/Metal remain long-term goals
    per CLAUDE.md, not wired into any adapter yet, so anything non-CUDA is
    treated the same as no GPU: CPU fallback, not a failure."""

    def __init__(self, gpu: GPUDetector) -> None:
        self._gpu = gpu

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        vram_gb = self._gpu.detect_vram_gb()
        if vram_gb is None:
            plan.compute_device = "cpu"
            prompter.info(
                "No CUDA GPU detected — Mémai will run STT and TTS on CPU "
                "(slower, but fully functional). Ollama's LLM inference is "
                "unaffected — it detects and uses any available GPU acceleration on its own."
            )
        else:
            plan.compute_device = "cuda"


class SelectLLM:
    """Flow steps 6-7. Presents every catalogue entry with a plain-English fit
    hint — never filters the list, per CLAUDE.md ("user always sees all
    options, never a filtered list"). Pulls the chosen model via Ollama before
    returning, matching the original flow doc's step 5 ("LLM selection +
    ollama pull") — the wizard should leave Ollama actually holding the model,
    not just record a choice in the plan."""

    def __init__(self, catalogues: CatalogueRepository, gpu: GPUDetector, installer: ModelInstaller) -> None:
        self._catalogues = catalogues
        self._gpu = gpu
        self._installer = installer

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        vram_gb = self._gpu.detect_vram_gb()
        if vram_gb is None:
            prompter.info(
                "Could not detect NVIDIA/CUDA GPU VRAM — sizing hints below are best-effort. "
                "This does not mean no GPU will be used: Ollama detects and uses AMD GPUs on "
                "its own for the LLM, independent of this check."
            )

        entries = self._catalogues.load_llm_catalogue()
        choices = []
        for entry in entries:
            fit = assess_fit(entry.vram, vram_gb, LLM_SELECTION_HEADROOM_GB)
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
        try:
            self._installer.pull_llm(plan.llm_model_id)
        except (OSError, subprocess.SubprocessError) as exc:
            proceed = prompter.confirm(
                f"Could not pull '{plan.llm_model_id}' via Ollama ({exc}). Continue anyway?",
                default=False,
            )
            if not proceed:
                raise WizardAborted(
                    f"Installation cancelled — pull '{plan.llm_model_id}' manually "
                    f"(`ollama pull {plan.llm_model_id}`) and re-run memai-setup."
                ) from exc


class SelectLanguages:
    """Flow step 8. Offers languages covered by at least one installable STT
    engine and at least one TTS engine (see domain/language_coverage.py).
    Multi-select — this is deliberately "which languages should Mémai support,"
    covering both your main language and any optional/secondary ones in one
    go (CLAUDE.md: secondary languages are tracked but switching between them
    is always explicit). It does NOT ask which one is primary — that choice
    happens live during your first conversation (onboarding; see CLAUDE.md
    WebSocket protocol's select_language/language_selected messages). This
    step decides which languages get engines/voices installed; the selection
    is persisted to memai.toml as [languages].installed (FR-705) — the server
    offers onboarding selection and response-language mirroring only within
    this set, and adding a language later means re-running this wizard."""

    def __init__(self, catalogues: CatalogueRepository) -> None:
        self._catalogues = catalogues

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        offered = sorted(
            offered_languages(self._catalogues.load_stt_catalogue(), self._catalogues.load_tts_catalogue())
        )
        choices = [PromptChoice(code, format_language(code)) for code in offered]
        plan.languages = prompter.select_many(
            "Which languages should Mémai understand and speak? Select your main language plus "
            "any others you might also use — you'll pick which one to start with during your "
            "first conversation.",
            choices,
        )


class ResolveSTTEngine:
    """Flow step 9. Mainly a Whisper model-size choice today (VRAM vs.
    accuracy/latency tradeoff — see large-v3-turbo in stt_catalogue.toml), not
    an engine choice, since faster-whisper covers ~99 languages
    unconditionally. Engines without `has_adapter` (e.g. whisper.cpp) are
    filtered out — catalogued but not yet installable, see catalogue comment.
    Reserves headroom for the already-chosen LLM (looked up by
    plan.llm_model_id, which SelectLLM must have set) plus TTS, rather than a
    flat guess — see STT_SELECTION_TTS_HEADROOM_GB."""

    def __init__(self, catalogues: CatalogueRepository, gpu: GPUDetector, installer: ModelInstaller) -> None:
        self._catalogues = catalogues
        self._gpu = gpu
        self._installer = installer

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        available_engines = [e for e in self._catalogues.load_stt_catalogue() if e.has_adapter]
        if not available_engines:
            prompter.info("No installable STT engine is catalogued — skipping STT setup.")
            return
        engine = available_engines[0]  # only one exists today; revisit if a second adapter ever lands

        vram_gb = self._gpu.detect_vram_gb()
        reserved_gb = STT_SELECTION_TTS_HEADROOM_GB
        llm_entry = next((e for e in self._catalogues.load_llm_catalogue() if e.model_id == plan.llm_model_id), None)
        if llm_entry is not None:
            reserved_gb += llm_entry.vram.recommended_gb

        choices = []
        for model in engine.models:
            fit = assess_fit(model.vram, vram_gb, reserved_gb)
            warning = "" if fit.level != FitLevel.WONT_FIT else " ⚠"
            recommended = " (recommended)" if model.recommended else ""
            choices.append(PromptChoice(model.name, f"{model.name}{recommended} — {fit.message}{warning}"))

        plan.whisper_model = prompter.select(f"Choose a Whisper model size ({engine.engine}):", choices)
        try:
            self._installer.download_whisper_model(plan.whisper_model)
        except Exception as exc:  # noqa: BLE001 — huggingface_hub can raise many exception types on network failure
            # Non-fatal: FasterWhisperSTTService triggers the same download lazily on
            # first use (see model_installer.py), so a failure here just means the
            # first server startup will be slower, not that the install is broken.
            prompter.info(
                f"Could not pre-download the '{plan.whisper_model}' Whisper model ({exc}). "
                "It will be downloaded automatically the first time the server starts instead."
            )


class ResolveTTSEngines:
    """Flow step 10. Per selected language, if only one engine covers it,
    install that one; if multiple engines cover it (e.g. both Kokoro and Piper
    offer English), let the user pick rather than silently defaulting to one —
    voice variety/quality is a stated goal, not just coverage. Bundled engines
    (Kokoro) need no download; per-voice engines (Piper) download only the
    voice for that language — additive, never removed. Coqui/XTTS stays out
    for now (licence conflict) but isn't ruled out long-term — see
    project_tts_license_conflict memory and catalogues/tts_catalogue.toml."""

    def __init__(self, catalogues: CatalogueRepository, installer: ModelInstaller) -> None:
        self._catalogues = catalogues
        self._installer = installer

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        entries = self._catalogues.load_tts_catalogue()
        for language in plan.languages:
            covering = [e for e in entries if "*" in e.languages or language in e.languages]
            if not covering:
                prompter.info(f"No TTS engine covers {format_language(language)} — skipping voice install.")
                continue

            if len(covering) == 1:
                engine = covering[0]
            else:
                choice = prompter.select(
                    f"Multiple voices are available for {format_language(language)} — which would you like?",
                    [PromptChoice(e.engine, f"{e.engine} ({e.licence})") for e in covering],
                )
                engine = next(e for e in covering if e.engine == choice)

            plan.tts_engine_by_language[language] = engine.engine
            if not engine.bundled:
                voice = next((v for v in engine.voices if v.language == language), None)
                if voice is not None:
                    try:
                        self._installer.download_piper_voice(voice.voice_id)
                    except Exception as exc:  # noqa: BLE001 — network calls can fail in many ways
                        # Non-fatal, matching ModelInstaller's re-runnable/idempotent contract
                        # (CLAUDE.md) — re-running memai-setup will retry this download.
                        prompter.info(
                            f"Could not download the '{voice.voice_id}' voice for "
                            f"{format_language(language)} ({exc}). Re-run memai-setup later to retry."
                        )
                else:
                    prompter.info(
                        f"{engine.engine} covers {format_language(language)} but no specific voice is "
                        "catalogued yet — install manually."
                    )


class DownloadEmbeddingModel:
    """Flow step 10b. Pre-downloads the embedding model used for memory
    consolidation (`intfloat/multilingual-e5-large`, hardcoded in
    SentenceTransformerEmbeddingService). Unlike the LLM/Whisper/TTS steps
    above, there is nothing to choose here — the embedding model is a fixed
    invariant of Mémai (CLAUDE.md: not voice-configurable, not swappable),
    so this step takes no user input and doesn't touch the plan. It exists
    only so first server startup doesn't have to hit the network for it:
    SentenceTransformerEmbeddingService forces HF_HUB_OFFLINE=1 on the live
    server, so an un-pre-downloaded model fails outright there instead of
    just being slower."""

    def __init__(self, installer: ModelInstaller) -> None:
        self._installer = installer

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        try:
            self._installer.download_embedding_model()
        except Exception as exc:  # noqa: BLE001 — network calls can fail in many ways
            prompter.info(
                f"Could not pre-download the embedding model ({exc}). The server will fail "
                "to start until it's downloaded — re-run memai-setup later to retry, or "
                "download it manually before starting the server."
            )


class GenerateConfig:
    """Flow step 11. Single-host also writes the client config (step 10b in the
    original flow doc — see project_wizard_brainstorm memory); split-host
    defers client config to a separate `memai-setup --client` run on the
    client machine (not yet wired into cli.py). Writes a couple of fields
    InstallationPlan doesn't have a dedicated collection step for yet
    (plan.database_url defaults to the same connection string shipped in
    server/config/memai.example.toml) — no "collect Postgres connection"
    step exists, see docs/PLAN.md."""

    def __init__(self, writer: ConfigWriter) -> None:
        self._writer = writer

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        self._writer.write_server_config(plan)
        if plan.topology is Topology.SINGLE_HOST:
            self._writer.write_client_config(plan)
        else:
            prompter.info("Split-host topology: run `memai-setup --client` on the client machine to finish setup.")


class SetupSchema:
    """Flow step 12. Delegates to SchemaRunner, which must apply
    migrations/001_initial_schema.sql idempotently — the SQL itself uses
    `IF NOT EXISTS`/`ON CONFLICT DO NOTHING` throughout, so a straightforward
    re-apply is safe with no migration framework needed."""

    def __init__(self, schema_runner: SchemaRunner) -> None:
        self._schema_runner = schema_runner

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        prompter.info("Applying database schema...")
        self._schema_runner.apply_schema(plan.database_url)


class RunHealthChecks:
    """Flow step 13. Runs a list of HealthCheck instances (currently just
    Ollama) and reports pass/fail via prompter. Postgres isn't re-checked
    here — ConfigureDatabaseConnection already verified it thoroughly, and
    SetupSchema (just before this step) would have failed loudly if the
    connection broke in between. No server-WebSocket check either — the
    wizard never starts memai-server itself, so that would always fail right
    after a fresh install (see cli.py's health_checks comment); main() tells
    the user how to start it instead. The concrete checks live in
    infrastructure/health_checks.py — this step is just the aggregator,
    unit-testable with Fake HealthChecks."""

    def __init__(self, checks: list[HealthCheck]) -> None:
        self._checks = checks

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        prompter.info("Running health checks...")
        for check in self._checks:
            result = check.check()
            status = "OK" if result.ok else "FAILED"
            prompter.info(f"[{status}] {check.name}: {result.message}")
