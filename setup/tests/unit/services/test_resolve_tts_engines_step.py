from memai_setup.domain.model import TTSCatalogueEntry, TTSVoiceEntry
from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.steps import ResolveTTSEngines

from tests.fakes.fakes import FakeCatalogueRepository, FakeModelInstaller, FakeWizardPrompter


def test_single_covering_engine_installs_without_prompting():
    tts_entries = (
        TTSCatalogueEntry(
            engine="kokoro", licence="Apache-2.0", languages=frozenset({"en"}), voices=(), bundled=True, description=""
        ),
    )
    catalogues = FakeCatalogueRepository(tts_entries=tts_entries)
    installer = FakeModelInstaller()
    step = ResolveTTSEngines(catalogues, installer)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan(languages=["en"])

    step.run(plan, prompter)

    assert plan.tts_engine_by_language == {"en": "kokoro"}
    assert installer.downloaded_piper_voices == []  # bundled, no download needed


def test_multiple_covering_engines_prompts_and_downloads_voice_for_choice():
    tts_entries = (
        TTSCatalogueEntry(
            engine="kokoro", licence="Apache-2.0", languages=frozenset({"en"}), voices=(), bundled=True, description=""
        ),
        TTSCatalogueEntry(
            engine="piper",
            licence="MIT",
            languages=frozenset({"en"}),
            voices=(TTSVoiceEntry("en_US-lessac-medium", "en", "Lessac"),),
            bundled=False,
            description="",
        ),
    )
    catalogues = FakeCatalogueRepository(tts_entries=tts_entries)
    installer = FakeModelInstaller()
    step = ResolveTTSEngines(catalogues, installer)
    prompter = FakeWizardPrompter(select_answers=["piper"])
    plan = InstallationPlan(languages=["en"])

    step.run(plan, prompter)

    assert plan.tts_engine_by_language == {"en": "piper"}
    assert installer.downloaded_piper_voices == ["en_US-lessac-medium"]


def test_download_failure_warns_but_does_not_raise():
    tts_entries = (
        TTSCatalogueEntry(
            engine="piper",
            licence="MIT",
            languages=frozenset({"en"}),
            voices=(TTSVoiceEntry("en_US-lessac-medium", "en", "Lessac"),),
            bundled=False,
            description="",
        ),
    )
    catalogues = FakeCatalogueRepository(tts_entries=tts_entries)
    installer = FakeModelInstaller(fail_download_piper_voice=True)
    step = ResolveTTSEngines(catalogues, installer)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan(languages=["en"])

    step.run(plan, prompter)  # must not raise

    assert plan.tts_engine_by_language == {"en": "piper"}
    assert installer.downloaded_piper_voices == []
    assert any("Could not download" in m for m in prompter.info_messages)


def test_no_covering_engine_skips_with_info_message():
    catalogues = FakeCatalogueRepository(tts_entries=())
    step = ResolveTTSEngines(catalogues, FakeModelInstaller())
    prompter = FakeWizardPrompter()
    plan = InstallationPlan(languages=["ko"])

    step.run(plan, prompter)

    assert plan.tts_engine_by_language == {}
    assert any("No TTS engine covers" in m for m in prompter.info_messages)
