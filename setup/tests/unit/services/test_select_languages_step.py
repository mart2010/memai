from memai_setup.domain.model import STTCatalogueEntry, TTSCatalogueEntry, VRAMEstimate, WhisperModelEntry
from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.steps import SelectLanguages

from tests.fakes.fakes import FakeCatalogueRepository, FakeWizardPrompter


def test_select_languages_offers_stt_tts_intersection_and_stores_selection():
    stt_entries = (
        STTCatalogueEntry(
            engine="faster-whisper",
            models=(WhisperModelEntry("small", VRAMEstimate(1, 2), recommended=True),),
            languages=frozenset({"*"}),
            has_adapter=True,
            description="",
        ),
    )
    tts_entries = (
        TTSCatalogueEntry(
            engine="kokoro",
            licence="Apache-2.0",
            languages=frozenset({"en", "fr"}),
            voices=(),
            bundled=True,
            description="",
        ),
    )
    catalogues = FakeCatalogueRepository(stt_entries=stt_entries, tts_entries=tts_entries)
    step = SelectLanguages(catalogues)

    captured = {}

    class RecordingPrompter(FakeWizardPrompter):
        def select_many(self, message, choices):
            captured["message"] = message
            return super().select_many(message, choices)

    prompter = RecordingPrompter(select_many_answers=[["en"]])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.languages == ["en"]
    # Prompt must clarify this covers main + optional languages together, and
    # that "which one is primary" is decided later during onboarding.
    assert "main language" in captured["message"].lower()
    assert "first conversation" in captured["message"].lower()


def _catalogues_offering(*codes: str) -> FakeCatalogueRepository:
    stt_entries = (
        STTCatalogueEntry(
            engine="faster-whisper",
            models=(WhisperModelEntry("small", VRAMEstimate(1, 2), recommended=True),),
            languages=frozenset({"*"}),
            has_adapter=True,
            description="",
        ),
    )
    tts_entries = (
        TTSCatalogueEntry(
            engine="kokoro", licence="Apache-2.0", languages=frozenset(codes),
            voices=(), bundled=True, description="",
        ),
    )
    return FakeCatalogueRepository(stt_entries=stt_entries, tts_entries=tts_entries)


def test_rerun_pre_checks_already_installed_languages():
    """FR-706 — re-run pre-fill: recorded languages come pre-checked, so adding
    one never silently drops the rest of [languages].installed."""
    step = SelectLanguages(_catalogues_offering("en", "fr", "it"))
    prompter = FakeWizardPrompter(select_many_answers=[["en", "fr", "it"]])
    plan = InstallationPlan(languages=["en", "fr"], from_existing_install=True)

    step.run(plan, prompter)

    _, choices = prompter.select_many_calls[0]
    checked = {c.value for c in choices if c.checked}
    assert checked == {"en", "fr"}
    assert plan.languages == ["en", "fr", "it"]
    assert any("pre-selected" in m for m in prompter.info_messages)


def test_fresh_run_pre_checks_nothing():
    step = SelectLanguages(_catalogues_offering("en", "fr"))
    prompter = FakeWizardPrompter(select_many_answers=[["en"]])
    plan = InstallationPlan()

    step.run(plan, prompter)

    _, choices = prompter.select_many_calls[0]
    assert not any(c.checked for c in choices)
    assert prompter.info_messages == []
