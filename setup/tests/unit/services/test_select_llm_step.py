from memai_setup.domain.model import LLMCatalogueEntry, VRAMEstimate
from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.steps import SelectLLM

from tests.fakes.fakes import FakeCatalogueRepository, FakeGPUDetector, FakeWizardPrompter


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
    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=24))
    prompter = FakeWizardPrompter(select_answers=["aya-expanse"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.llm_model_id == "aya-expanse"


def test_select_llm_warns_when_vram_undetectable():
    catalogues = FakeCatalogueRepository(llm_entries=(_entry("aya-expanse", "Aya Expanse", 5, 8, recommended=True),))
    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=None))
    prompter = FakeWizardPrompter(select_answers=["aya-expanse"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert any("could not detect" in m.lower() for m in prompter.info_messages)


def test_select_llm_flags_reasoning_models_in_choice_label():
    catalogues = FakeCatalogueRepository(
        llm_entries=(_entry("qwen3:14b", "Qwen3 14B", 10, 14, recommended=False, reasoning=True),)
    )
    captured_choices = {}

    class RecordingPrompter(FakeWizardPrompter):
        def select(self, message, choices):
            captured_choices["choices"] = choices
            return super().select(message, choices)

    step = SelectLLM(catalogues, FakeGPUDetector(vram_gb=24))
    prompter = RecordingPrompter(select_answers=["qwen3:14b"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert "reasoning model" in captured_choices["choices"][0].label
