from memai_setup.domain.model import LLMCatalogueEntry, STTCatalogueEntry, TTSCatalogueEntry
from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.ports import PromptChoice


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
    """Replays a pre-scripted sequence of answers. Pops in call order — script
    select()/select_many() answers in the order the wizard will ask them."""

    def __init__(self, select_answers: list[str] | None = None, confirm_answers: list[bool] | None = None) -> None:
        self._select_answers = list(select_answers or [])
        self._confirm_answers = list(confirm_answers or [])
        self.info_messages: list[str] = []

    def select(self, message: str, choices: list[PromptChoice]) -> str:
        return self._select_answers.pop(0)

    def select_many(self, message: str, choices: list[PromptChoice]) -> list[str]:
        return [self._select_answers.pop(0)]

    def confirm(self, message: str, default: bool = True) -> bool:
        return self._confirm_answers.pop(0) if self._confirm_answers else default

    def text(self, message: str, default: str = "") -> str:
        return default

    def info(self, message: str) -> None:
        self.info_messages.append(message)


class FakeExistingInstallDetector:
    def __init__(self, existing_plan: InstallationPlan | None = None) -> None:
        self._existing_plan = existing_plan

    def load_existing_plan(self) -> InstallationPlan | None:
        return self._existing_plan
