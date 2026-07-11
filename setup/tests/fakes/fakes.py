import subprocess

from memai_setup.domain.model import LLMCatalogueEntry, STTCatalogueEntry, TTSCatalogueEntry
from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.ports import HealthCheckResult, PromptChoice


class FakeGPUDetector:
    def __init__(self, vram_gb: float | None) -> None:
        self._vram_gb = vram_gb

    def detect_vram_gb(self) -> float | None:
        return self._vram_gb


class FakeCatalogueRepository:
    def __init__(
        self,
        llm_entries: tuple[LLMCatalogueEntry, ...] = (),
        stt_entries: tuple[STTCatalogueEntry, ...] = (),
        tts_entries: tuple[TTSCatalogueEntry, ...] = (),
    ) -> None:
        self._llm_entries = llm_entries
        self._stt_entries = stt_entries
        self._tts_entries = tts_entries

    def load_llm_catalogue(self) -> tuple[LLMCatalogueEntry, ...]:
        return self._llm_entries

    def load_stt_catalogue(self) -> tuple[STTCatalogueEntry, ...]:
        return self._stt_entries

    def load_tts_catalogue(self) -> tuple[TTSCatalogueEntry, ...]:
        return self._tts_entries


class FakeWizardPrompter:
    """Replays pre-scripted answers. Each of select()/select_many()/confirm()
    pops from its own queue, in the order the wizard will call them."""

    def __init__(
        self,
        select_answers: list[str] | None = None,
        select_many_answers: list[list[str]] | None = None,
        confirm_answers: list[bool] | None = None,
    ) -> None:
        self._select_answers = list(select_answers or [])
        self._select_many_answers = list(select_many_answers or [])
        self._confirm_answers = list(confirm_answers or [])
        self.info_messages: list[str] = []
        self.confirm_messages: list[str] = []
        self.headings: list[tuple[str, list[str]]] = []

    def select(self, message: str, choices: list[PromptChoice]) -> str:
        return self._select_answers.pop(0)

    def select_many(self, message: str, choices: list[PromptChoice]) -> list[str]:
        return self._select_many_answers.pop(0)

    def confirm(self, message: str, default: bool = True) -> bool:
        self.confirm_messages.append(message)
        return self._confirm_answers.pop(0) if self._confirm_answers else default

    def text(self, message: str, default: str = "") -> str:
        return default

    def info(self, message: str) -> None:
        self.info_messages.append(message)

    def heading(self, title: str, lines: list[str] | None = None) -> None:
        self.headings.append((title, list(lines or [])))


class FakeExistingInstallDetector:
    def __init__(self, existing_plan: InstallationPlan | None = None) -> None:
        self._existing_plan = existing_plan

    def load_existing_plan(self) -> InstallationPlan | None:
        return self._existing_plan


class FakeModelInstaller:
    """Records every install call instead of touching the network/Ollama."""

    def __init__(
        self,
        fail_pull_llm: bool = False,
        fail_download_whisper_model: bool = False,
        fail_download_piper_voice: bool = False,
    ) -> None:
        self.pulled_llms: list[str] = []
        self.downloaded_whisper_models: list[str] = []
        self.downloaded_piper_voices: list[str] = []
        self._fail_pull_llm = fail_pull_llm
        self._fail_download_whisper_model = fail_download_whisper_model
        self._fail_download_piper_voice = fail_download_piper_voice

    def pull_llm(self, model_id: str) -> None:
        if self._fail_pull_llm:
            raise subprocess.CalledProcessError(1, ["ollama", "pull", model_id])
        self.pulled_llms.append(model_id)

    def download_whisper_model(self, name: str) -> None:
        if self._fail_download_whisper_model:
            raise RuntimeError("simulated network failure")
        self.downloaded_whisper_models.append(name)

    def download_piper_voice(self, voice_id: str) -> None:
        if self._fail_download_piper_voice:
            raise RuntimeError("simulated network failure")
        self.downloaded_piper_voices.append(voice_id)


class FakeConfigWriter:
    """Records the plan passed to each write call instead of touching disk."""

    def __init__(self) -> None:
        self.server_config_writes: list[InstallationPlan] = []
        self.client_config_writes: list[InstallationPlan] = []

    def write_server_config(self, plan: InstallationPlan) -> None:
        self.server_config_writes.append(plan)

    def write_client_config(self, plan: InstallationPlan) -> None:
        self.client_config_writes.append(plan)


class FakeSchemaRunner:
    def __init__(self) -> None:
        self.applied_to: list[str] = []

    def apply_schema(self, database_url: str) -> None:
        self.applied_to.append(database_url)


class FakeConnectionVerifier:
    """Scripted DatabaseConnectionVerifier — no real Postgres involved.
    Defaults to both checks passing; override per-test via the constructor."""

    def __init__(
        self,
        postgres_ok: bool = True,
        postgres_message: str = "reachable",
        pgvector_ok: bool = True,
        pgvector_message: str = "installed",
    ) -> None:
        self._postgres_result = HealthCheckResult("Postgres", postgres_ok, postgres_message)
        self._pgvector_result = HealthCheckResult("pgvector extension", pgvector_ok, pgvector_message)
        self.verified_urls: list[str] = []

    def verify(self, database_url: str) -> tuple[HealthCheckResult, HealthCheckResult]:
        self.verified_urls.append(database_url)
        return self._postgres_result, self._pgvector_result


class FakeHealthCheck:
    def __init__(self, name: str, ok: bool, message: str = "") -> None:
        self.name = name
        self._result = HealthCheckResult(name, ok, message)

    def check(self) -> HealthCheckResult:
        return self._result
