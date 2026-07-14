import pytest

from memai_setup.domain.model import LLMCatalogueEntry, VRAMEstimate
from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.errors import WizardAborted
from memai_setup.services.steps import SelectLLM

from tests.fakes.fakes import FakeCatalogueRepository, FakeGPUDetector, FakeModelInstaller, FakeWizardPrompter


def _entry(model_id: str, display_name: str, min_gb: float, rec_gb: float, recommended: bool, reasoning: bool = False) -> LLMCatalogueEntry:
    return LLMCatalogueEntry(
        model_id=model_id,
        display_name=display_name,
        vram=VRAMEstimate(min_gb, rec_gb),
        languages=frozenset({"en"}),
        recommended=recommended,
        reasoning=reasoning,
        description="",
    )


def test_select_llm_stores_chosen_model_id():
    catalogues = FakeCatalogueRepository(
        llm_entries=(
            _entry("aya-expanse", "Aya Expanse", 5, 8, recommended=True),
            _entry("qwen3:14b", "Qwen3 14B", 10, 14, recommended=False, reasoning=True),
        )
    )
    installer = FakeModelInstaller()
    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=24), installer)
    prompter = FakeWizardPrompter(select_answers=["aya-expanse"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.llm_model_id == "aya-expanse"


def test_select_llm_pulls_the_chosen_model():
    catalogues = FakeCatalogueRepository(
        llm_entries=(
            _entry("aya-expanse", "Aya Expanse", 5, 8, recommended=True),
            _entry("qwen3:14b", "Qwen3 14B", 10, 14, recommended=False, reasoning=True),
        )
    )
    installer = FakeModelInstaller()
    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=24), installer)
    prompter = FakeWizardPrompter(select_answers=["aya-expanse"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert installer.pulled_llms == ["aya-expanse"]


def test_select_llm_pull_failure_declined_raises_wizard_aborted():
    catalogues = FakeCatalogueRepository(llm_entries=(_entry("aya-expanse", "Aya Expanse", 5, 8, recommended=True),))
    installer = FakeModelInstaller(fail_pull_llm=True)
    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=24), installer)
    prompter = FakeWizardPrompter(select_answers=["aya-expanse"], confirm_answers=[False])
    plan = InstallationPlan()

    with pytest.raises(WizardAborted):
        step.run(plan, prompter)


def test_select_llm_pull_failure_confirmed_continues():
    catalogues = FakeCatalogueRepository(llm_entries=(_entry("aya-expanse", "Aya Expanse", 5, 8, recommended=True),))
    installer = FakeModelInstaller(fail_pull_llm=True)
    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=24), installer)
    prompter = FakeWizardPrompter(select_answers=["aya-expanse"], confirm_answers=[True])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.llm_model_id == "aya-expanse"


def test_select_llm_warns_when_vram_undetectable():
    catalogues = FakeCatalogueRepository(llm_entries=(_entry("aya-expanse", "Aya Expanse", 5, 8, recommended=True),))
    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=None), FakeModelInstaller())
    prompter = FakeWizardPrompter(select_answers=["aya-expanse"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert any("could not detect" in m.lower() for m in prompter.info_messages)


def test_select_llm_flags_reasoning_models_in_choice_label():
    catalogues = FakeCatalogueRepository(
        llm_entries=(_entry("qwen3:14b", "Qwen3 14B", 10, 14, recommended=False, reasoning=True),)
    )
    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=24), FakeModelInstaller())
    prompter = FakeWizardPrompter(select_answers=["qwen3:14b"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    _, choices, _ = prompter.select_calls[0]
    assert "reasoning model" in choices[0].label


def test_select_llm_rerun_defaults_to_current_model_and_marks_it():
    """FR-706 — re-run pre-fill: the recorded model is the highlighted default
    and its label says so."""
    catalogues = FakeCatalogueRepository(
        llm_entries=(
            _entry("aya-expanse", "Aya Expanse", 5, 8, recommended=True),
            _entry("qwen3:14b", "Qwen3 14B", 10, 14, recommended=False, reasoning=True),
        )
    )
    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=24), FakeModelInstaller())
    prompter = FakeWizardPrompter(select_answers=["aya-expanse"])
    plan = InstallationPlan(llm_model_id="aya-expanse", from_existing_install=True)

    step.run(plan, prompter)

    _, choices, default = prompter.select_calls[0]
    assert default == "aya-expanse"
    assert "(current)" in choices[0].label
    assert "(current)" not in choices[1].label


def test_select_llm_fresh_run_has_no_default():
    catalogues = FakeCatalogueRepository(llm_entries=(_entry("aya-expanse", "Aya Expanse", 5, 8, recommended=True),))
    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=24), FakeModelInstaller())
    prompter = FakeWizardPrompter(select_answers=["aya-expanse"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    _, _, default = prompter.select_calls[0]
    assert default is None
