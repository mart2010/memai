from memai_setup.domain.model import LLMCatalogueEntry, STTCatalogueEntry, VRAMEstimate, WhisperModelEntry
from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.steps import ResolveSTTEngine

from tests.fakes.fakes import FakeCatalogueRepository, FakeGPUDetector, FakeModelInstaller, FakeWizardPrompter


def _llm_entry(model_id: str, rec_gb: float) -> LLMCatalogueEntry:
    return LLMCatalogueEntry(model_id, model_id, VRAMEstimate(rec_gb - 2, rec_gb), frozenset({"en"}), True, False, "")


def test_filters_out_engines_without_adapter_and_downloads_the_choice():
    stt_entries = (
        STTCatalogueEntry(
            engine="faster-whisper",
            models=(
                WhisperModelEntry("small", VRAMEstimate(1, 2), recommended=False),
                WhisperModelEntry("large-v3-turbo", VRAMEstimate(3, 6), recommended=True),
            ),
            languages=frozenset({"*"}),
            has_adapter=True,
            description="",
        ),
        STTCatalogueEntry(
            engine="whisper.cpp",
            models=(),
            languages=frozenset({"*"}),
            has_adapter=False,
            description="",
        ),
    )
    catalogues = FakeCatalogueRepository(llm_entries=(_llm_entry("aya-expanse", 8),), stt_entries=stt_entries)
    installer = FakeModelInstaller()
    step = ResolveSTTEngine(catalogues, FakeGPUDetector(vram_gb=24), installer)
    prompter = FakeWizardPrompter(select_answers=["large-v3-turbo"])
    plan = InstallationPlan(llm_model_id="aya-expanse")

    step.run(plan, prompter)

    assert plan.whisper_model == "large-v3-turbo"
    assert installer.downloaded_whisper_models == ["large-v3-turbo"]


def test_reserves_headroom_for_the_already_chosen_llm():
    # 12GB card, aya-expanse (recommended 8GB) already chosen + 2GB TTS
    # headroom leaves 2GB — large-v3-turbo (min 3GB) shouldn't fit.
    stt_entries = (
        STTCatalogueEntry(
            engine="faster-whisper",
            models=(WhisperModelEntry("large-v3-turbo", VRAMEstimate(3, 6), recommended=True),),
            languages=frozenset({"*"}),
            has_adapter=True,
            description="",
        ),
    )
    catalogues = FakeCatalogueRepository(llm_entries=(_llm_entry("aya-expanse", 8),), stt_entries=stt_entries)
    captured = {}

    class RecordingPrompter(FakeWizardPrompter):
        def select(self, message, choices):
            captured["choices"] = choices
            return super().select(message, choices)

    step = ResolveSTTEngine(catalogues, FakeGPUDetector(vram_gb=12), FakeModelInstaller())
    prompter = RecordingPrompter(select_answers=["large-v3-turbo"])
    plan = InstallationPlan(llm_model_id="aya-expanse")

    step.run(plan, prompter)

    assert "⚠" in captured["choices"][0].label


def test_download_failure_warns_but_does_not_raise():
    stt_entries = (
        STTCatalogueEntry(
            engine="faster-whisper",
            models=(WhisperModelEntry("small", VRAMEstimate(1, 2), recommended=True),),
            languages=frozenset({"*"}),
            has_adapter=True,
            description="",
        ),
    )
    catalogues = FakeCatalogueRepository(llm_entries=(_llm_entry("aya-expanse", 8),), stt_entries=stt_entries)
    installer = FakeModelInstaller(fail_download_whisper_model=True)
    step = ResolveSTTEngine(catalogues, FakeGPUDetector(vram_gb=24), installer)
    prompter = FakeWizardPrompter(select_answers=["small"])
    plan = InstallationPlan(llm_model_id="aya-expanse")

    step.run(plan, prompter)  # must not raise

    assert plan.whisper_model == "small"
    assert installer.downloaded_whisper_models == []
    assert any("Could not pre-download" in m for m in prompter.info_messages)


def test_no_available_engine_skips_with_info_message():
    stt_entries = (
        STTCatalogueEntry(engine="whisper.cpp", models=(), languages=frozenset({"*"}), has_adapter=False, description=""),
    )
    catalogues = FakeCatalogueRepository(stt_entries=stt_entries)
    step = ResolveSTTEngine(catalogues, FakeGPUDetector(vram_gb=24), FakeModelInstaller())
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.whisper_model is None
    assert any("No installable STT engine" in m for m in prompter.info_messages)
