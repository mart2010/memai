# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import getpass
import subprocess
import sys
from typing import Protocol

from ..domain.language_coverage import offered_languages
from ..domain.languages import format_language
from ..domain.model import FitLevel
from ..domain.plan import InstallationPlan, Topology, masked_database_url
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
        "and a 'memai' role/database created — see docs/INSTALLATION.md. The next step "
        "prefers passwordless OS-credential auth (peer on Linux/macOS, SSPI on Windows): "
        "if your OS user isn't yet mapped to the 'memai' role in pg_ident.conf, that step "
        "will show you the exact lines to add",
        "Ollama installed and running",
        "(Optional) A GPU speeds things up. NVIDIA (CUDA 12 + cuDNN 9) accelerates "
        "STT, TTS, and the LLM. AMD GPUs are auto-detected by Ollama and accelerate "
        "the LLM only — STT/TTS have no AMD-accelerated path yet and always run on CPU",
        "With no GPU at all, everything still works: STT/TTS run comfortably on CPU "
        "(benchmarked fast on modern hardware); the offline memory pipeline (consolidation, "
        "memory brief) also runs fine on CPU via Ollama, just slower. Live conversation can "
        "either use local Ollama on CPU too (noticeably slower depending on model size) or, "
        "a few steps from now, a remote OpenAI-compatible API instead — your choice",
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
    documents on failure. Windows has no `peer` auth at all — PostgreSQL's
    own docs are explicit that it needs getpeereid()/SO_PEERCRED, which
    Windows doesn't provide — so this step offers `sspi` there instead
    (verified 2026-07-17): Windows' native single-sign-on mechanism, which
    PostgreSQL negotiates over a loopback TCP connection (`host`, not
    `local` — Windows has no peer-credential-bearing Unix socket) and which
    works for a local, non-domain-joined account too (falls back to NTLM
    when no Kerberos realm is available), not just domain machines. Same
    shape as peer auth otherwise: a `pg_ident.conf` mapping from OS username
    to the fixed "memai" role, documented on failure. Falls back to
    host+password for remote/non-standard setups either way."""

    _PEER_AUTH_URL = "postgresql:///memai?user=memai"
    _SSPI_AUTH_URL = "postgresql://memai@localhost:5432/memai"

    def __init__(self, verifier: DatabaseConnectionVerifier) -> None:
        self._verifier = verifier

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        on_windows = sys.platform.startswith("win")
        os_auth_choice = (
            PromptChoice("sspi", "Local SSPI authentication (recommended) — no password stored")
            if on_windows
            else PromptChoice("peer", "Local peer authentication (recommended) — no password stored")
        )
        choices = [
            os_auth_choice,
            PromptChoice("password", "Host + password (remote or custom Postgres setup)"),
        ]
        default = None
        if plan.from_existing_install:
            # Re-run (FR-706): keeping the recorded connection is the default; it is
            # still verified below like any freshly entered one.
            choices.insert(
                0, PromptChoice("keep", f"Keep current connection — {masked_database_url(plan.database_url)}")
            )
            default = "keep"
        choice = prompter.select("How should Mémai connect to PostgreSQL?", choices, default=default)
        if choice == "keep":
            database_url = plan.database_url
            failure_hint = self._failure_hint_for(database_url)
        elif choice == "peer":
            database_url = self._PEER_AUTH_URL
            failure_hint = self._peer_auth_hint()
        elif choice == "sspi":
            database_url = self._SSPI_AUTH_URL
            failure_hint = self._sspi_auth_hint()
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

    def _failure_hint_for(self, database_url: str) -> str:
        if database_url == self._PEER_AUTH_URL:
            return self._peer_auth_hint()
        if database_url == self._SSPI_AUTH_URL:
            return self._sspi_auth_hint()
        return ""

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

    def _sspi_auth_hint(self) -> str:
        os_user = getpass.getuser()
        return (
            " This usually means the 'memai' role isn't mapped to your Windows user "
            f"('{os_user}') yet — add to pg_ident.conf:\n"
            f"  memai_map    {os_user}    memai\n"
            "and to pg_hba.conf (before any catch-all 'host' line):\n"
            "  host   memai   memai   127.0.0.1/32   sspi map=memai_map\n"
            "  host   memai   memai   ::1/128        sspi map=memai_map\n"
            "then restart the PostgreSQL service (Services app, or an admin PowerShell: "
            "`Restart-Service postgresql-x64-<version>`) and re-run memai-setup."
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

    detect_vram_gb() only ever recognizes NVIDIA/CUDA (see
    infrastructure/gpu.py), so None here covers both "no GPU" and "GPU
    present but not NVIDIA" (e.g. AMD) — ROCm/Metal remain long-term goals
    per CLAUDE.md, not wired into any adapter yet, so anything non-CUDA is
    treated the same as no GPU for compute_device: CPU fallback, not a
    failure. The two cases are told apart only in the message shown to the
    user, via detect_gpu()'s Linux-sysfs fallback — real testing on an AMD
    Ryzen AI APU box found the wizard reporting a flat "no GPU detected" even
    though Ollama was accelerating the LLM on it fine; this doesn't change
    compute_device (no ROCm STT/TTS adapter exists yet), just stops the
    message from implying nothing is there at all."""

    def __init__(self, gpu: GPUDetector) -> None:
        self._gpu = gpu

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        if self._gpu.detect_vram_gb() is not None:
            plan.compute_device = "cuda"
            return

        plan.compute_device = "cpu"
        detected = self._gpu.detect_gpu()
        if detected is not None and detected.vendor != "unknown":
            memory_note = f" (~{detected.vram_gb:.0f} GB)" if detected.vram_gb is not None else ""
            prompter.info(
                f"No NVIDIA/CUDA GPU detected, but a {detected.vendor.upper()} GPU{memory_note} was "
                "found — Mémai will run STT and TTS on CPU (no AMD-accelerated path for those yet), "
                "but Ollama's LLM inference can still use this GPU on its own."
            )
        else:
            prompter.info(
                "No GPU detected — Mémai will run STT and TTS on CPU "
                "(slower, but fully functional). Ollama's LLM inference is "
                "unaffected — it detects and uses any available GPU acceleration on its own."
            )


class ConfigureLLMProvider:
    """Flow step 6, before SelectLLM's Ollama catalogue (now step 7) — decides
    how LIVE conversation is powered (FR-707/TR-955): local via Ollama
    (default, fully private), or a remote OpenAI-compatible HTTP endpoint
    (OpenRouter, OpenAI, a self-hosted vLLM/LM Studio server, ...) for
    installs without a local GPU capable of fast live inference. Minimal
    remote config, per design discussion: base_url + model name, optional
    api_key (some self-hosted endpoints don't check one at all — a blank
    answer is stored as None, not an empty string).

    Does not touch the offline memory pipeline at all — that always runs on
    a local Ollama model regardless of this choice, picked next by
    SelectLLM whether or not this step went remote."""

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        ollama_current = " (current)" if plan.from_existing_install and plan.llm_provider == "ollama" else ""
        remote_current = (
            " (current)" if plan.from_existing_install and plan.llm_provider == "openai_compatible" else ""
        )
        choices = [
            PromptChoice(
                "ollama",
                f"Local, via Ollama{ollama_current} (fully private, needs a decent local GPU or CPU)",
            ),
            PromptChoice(
                "openai_compatible",
                f"Remote OpenAI-compatible API{remote_current} (OpenRouter, OpenAI, a self-hosted "
                "endpoint, ...) — faster live conversation without a local GPU, at the cost of "
                "sending conversation text to that provider",
            ),
        ]
        plan.llm_provider = prompter.select(
            "How should live conversation be powered?", choices, default=plan.llm_provider
        )
        if plan.llm_provider != "openai_compatible":
            return

        plan.llm_base_url = prompter.text(
            "Endpoint base URL (OpenAI-compatible, e.g. https://openrouter.ai/api/v1):",
            default=plan.llm_base_url or "",
        )
        plan.llm_remote_model = prompter.text(
            "Model name, exactly as the endpoint expects it "
            "(e.g. meta-llama/llama-3.3-70b-instruct):",
            default=plan.llm_remote_model or "",
        )
        api_key = prompter.text(
            "API key (leave blank if the endpoint doesn't require one):",
            default=plan.llm_api_key or "",
        )
        plan.llm_api_key = api_key or None
        prompter.info(
            "The offline memory pipeline (consolidation, memory brief) always uses a local "
            "Ollama model regardless of this choice — you'll pick that next."
        )


class SelectLLM:
    """Flow steps 7-8. Presents every catalogue entry with a plain-English fit
    hint — never filters the list, per CLAUDE.md ("user always sees all
    options, never a filtered list"). Pulls the chosen model via Ollama before
    returning, matching the original flow doc's step 5 ("LLM selection +
    ollama pull") — the wizard should leave Ollama actually holding the model,
    not just record a choice in the plan.

    Unlike DetectComputeDevice, a non-NVIDIA GPU identified via
    gpu.detect_gpu() *does* feed into this step's own sizing math (not just
    its message) when it carries a memory estimate — Ollama genuinely uses
    that GPU for LLM inference (confirmed on a real AMD Ryzen AI APU box),
    unlike STT/TTS, which have no non-CUDA accelerated path at all.

    Always runs, regardless of what ConfigureLLMProvider (previous step)
    decided for live conversation (FR-707/TR-955): the offline memory
    pipeline (consolidation, memory brief, tutor strategy helpers)
    unconditionally needs a local Ollama model, even on an install whose
    live conversation is powered by a remote endpoint instead. The prompt
    text says so explicitly in that case, so choosing a model here doesn't
    read as contradicting the remote choice just made."""

    def __init__(self, catalogues: CatalogueRepository, gpu: GPUDetector, installer: ModelInstaller) -> None:
        self._catalogues = catalogues
        self._gpu = gpu
        self._installer = installer

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        vram_gb = self._gpu.detect_vram_gb()
        detected = None
        if vram_gb is None:
            detected = self._gpu.detect_gpu()
            if detected is not None and detected.vram_gb is not None:
                vram_gb = detected.vram_gb

        if vram_gb is None:
            prompter.info(
                "Could not detect GPU memory — sizing hints below are best-effort. "
                "This does not mean no GPU will be used: Ollama detects and uses "
                "available GPUs on its own for the LLM, independent of this check."
            )
        elif detected is not None:
            prompter.info(
                f"Detected a {detected.vendor.upper()} GPU (~{vram_gb:.0f} GB) — sizing hints below "
                "use this estimate; Ollama uses this GPU for the LLM on its own, independent of "
                "this check."
            )

        entries = self._catalogues.load_llm_catalogue()
        choices = []
        for entry in entries:
            fit = assess_fit(entry.vram, vram_gb, LLM_SELECTION_HEADROOM_GB)
            warning = "" if fit.level != FitLevel.WONT_FIT else " ⚠"
            recommended = " (recommended)" if entry.recommended else ""
            current = " (current)" if entry.model_id == plan.llm_model_id else ""
            # Enforced structurally, not left to catalogue description prose —
            # see the `reasoning` field comment in domain/model.py.
            reasoning_warning = " ⚠ reasoning model — <think> block is spoken aloud" if entry.reasoning else ""
            choices.append(
                PromptChoice(
                    entry.model_id,
                    f"{entry.display_name}{current}{recommended} — {fit.message}{warning}{reasoning_warning}",
                )
            )

        prompt_text = (
            "Choose a local Ollama model for the offline memory pipeline "
            "(consolidation, memory brief) — live conversation uses the remote endpoint "
            "just configured:"
            if plan.llm_provider == "openai_compatible"
            else "Choose a language model:"
        )
        # On a re-run the currently installed model is the highlighted default
        # (FR-706); on a fresh run llm_model_id is None and no default applies.
        plan.llm_model_id = prompter.select(prompt_text, choices, default=plan.llm_model_id)
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
    """Flow step 9. Offers languages covered by at least one installable STT
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
    this set, and adding a language later means re-running this wizard.
    On a re-run (plan pre-filled from the existing config, FR-706) the
    already-installed languages come pre-checked, so adding one never
    silently drops the rest of [languages].installed."""

    def __init__(self, catalogues: CatalogueRepository) -> None:
        self._catalogues = catalogues

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        offered = sorted(
            offered_languages(self._catalogues.load_stt_catalogue(), self._catalogues.load_tts_catalogue())
        )
        already_installed = set(plan.languages)
        if already_installed:
            prompter.info(
                "Already-installed languages are pre-selected — check more to add them. "
                "Unchecking one removes it from the server's installed list (its voice "
                "files stay on disk)."
            )
        choices = [
            PromptChoice(code, format_language(code), checked=code in already_installed) for code in offered
        ]
        plan.languages = prompter.select_many(
            "Which languages should Mémai understand and speak? Select your main language plus "
            "any others you might also use — you'll pick which one to start with during your "
            "first conversation.",
            choices,
        )


class ResolveSTTEngine:
    """Flow step 10. Mainly a Whisper model-size choice today (VRAM vs.
    accuracy/latency tradeoff — see large-v3-turbo in stt_catalogue.toml), not
    an engine choice, since faster-whisper covers ~99 languages
    unconditionally. Engines without `has_adapter` (e.g. whisper.cpp) are
    filtered out — catalogued but not yet installable, see catalogue comment.
    Reserves headroom for the already-chosen LLM (looked up by
    plan.llm_model_id, which SelectLLM must have set) plus TTS, rather than a
    flat guess — see STT_SELECTION_TTS_HEADROOM_GB.

    Deliberately stays on detect_vram_gb() alone, unlike SelectLLM — Whisper
    always runs on CPU without a CUDA GPU (no ROCm/other accelerated STT
    adapter exists), so a non-NVIDIA GPU's memory has no bearing on this
    model-size choice the way it does for the LLM, which Ollama can actually
    place on such a GPU."""

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

        # Re-run default (FR-706): the currently configured model, when it is one of
        # the catalogued sizes (a hand-edited model_path pointing at a directory
        # simply matches nothing and no default applies).
        plan.whisper_model = prompter.select(
            f"Choose a Whisper model size ({engine.engine}):", choices, default=plan.whisper_model
        )
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
    """Flow step 11. Per selected language, if only one engine covers it,
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
    """Flow step 11b. Pre-downloads the embedding model used for memory
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
    """Flow step 12. Single-host also writes the client config (step 10b in the
    original flow doc — see project_wizard_brainstorm memory); split-host
    defers client config to a separate `memai-setup --client` run on the
    client machine (not yet wired into cli.py)."""

    def __init__(self, writer: ConfigWriter) -> None:
        self._writer = writer

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        self._writer.write_server_config(plan)
        if plan.topology is Topology.SINGLE_HOST:
            self._writer.write_client_config(plan)
        else:
            prompter.info("Split-host topology: run `memai-setup --client` on the client machine to finish setup.")


class SetupSchema:
    """Flow step 13. Delegates to SchemaRunner, which must apply
    migrations/001_initial_schema.sql idempotently — the SQL itself uses
    `IF NOT EXISTS`/`ON CONFLICT DO NOTHING` throughout, so a straightforward
    re-apply is safe with no migration framework needed."""

    def __init__(self, schema_runner: SchemaRunner) -> None:
        self._schema_runner = schema_runner

    def run(self, plan: InstallationPlan, prompter: WizardPrompter) -> None:
        prompter.info("Applying database schema...")
        self._schema_runner.apply_schema(plan.database_url)


class RunHealthChecks:
    """Flow step 14. Runs a list of HealthCheck instances (currently just
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
