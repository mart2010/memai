from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.steps import ConfigureLLMProvider

from tests.fakes.fakes import FakeWizardPrompter


def test_local_ollama_choice_sets_provider_and_asks_nothing_else():
    step = ConfigureLLMProvider()
    prompter = FakeWizardPrompter(select_answers=["ollama"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.llm_provider == "ollama"
    assert plan.llm_base_url is None
    assert plan.llm_remote_model is None
    assert plan.llm_api_key is None


def test_remote_choice_collects_base_url_and_remote_model():
    """Spec: FR-707 — the Fake's text() always returns whatever default it's
    given, so this exercises the wiring (right value assigned to the right
    plan field) via re-run pre-fill defaults, same pattern as
    test_configure_database_connection_step.py."""
    step = ConfigureLLMProvider()
    prompter = FakeWizardPrompter(select_answers=["openai_compatible"])
    plan = InstallationPlan(
        llm_base_url="https://openrouter.ai/api/v1",
        llm_remote_model="meta-llama/llama-3.3-70b-instruct",
    )

    step.run(plan, prompter)

    assert plan.llm_provider == "openai_compatible"
    assert plan.llm_base_url == "https://openrouter.ai/api/v1"
    assert plan.llm_remote_model == "meta-llama/llama-3.3-70b-instruct"


def test_remote_choice_with_blank_api_key_stores_none_not_empty_string():
    """Spec: FR-707 — some self-hosted OpenAI-compatible endpoints don't
    require a key at all; a blank answer must not become an empty-string key."""
    step = ConfigureLLMProvider()
    prompter = FakeWizardPrompter(select_answers=["openai_compatible"])
    plan = InstallationPlan(llm_api_key=None)

    step.run(plan, prompter)

    assert plan.llm_api_key is None


def test_remote_choice_keeps_pre_filled_api_key_on_rerun():
    step = ConfigureLLMProvider()
    prompter = FakeWizardPrompter(select_answers=["openai_compatible"])
    plan = InstallationPlan(llm_api_key="sk-example", from_existing_install=True)

    step.run(plan, prompter)

    assert plan.llm_api_key == "sk-example"


def test_remote_choice_mentions_offline_pipeline_stays_local():
    step = ConfigureLLMProvider()
    prompter = FakeWizardPrompter(select_answers=["openai_compatible"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert any("local" in m.lower() and "ollama" in m.lower() for m in prompter.info_messages)


def test_local_choice_prints_no_offline_pipeline_note():
    step = ConfigureLLMProvider()
    prompter = FakeWizardPrompter(select_answers=["ollama"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert prompter.info_messages == []


def test_rerun_offers_current_provider_as_default():
    """FR-706-style re-run pre-fill, matching SelectLLM/ConfigureDatabaseConnection's
    established convention for this wizard."""
    step = ConfigureLLMProvider()
    prompter = FakeWizardPrompter(select_answers=["ollama"])
    plan = InstallationPlan(llm_provider="ollama", from_existing_install=True)

    step.run(plan, prompter)

    _, choices, default = prompter.select_calls[0]
    assert default == "ollama"
    assert "(current)" in choices[0].label
    assert "(current)" not in choices[1].label


def test_fresh_run_has_no_current_marker():
    step = ConfigureLLMProvider()
    prompter = FakeWizardPrompter(select_answers=["ollama"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    _, choices, _ = prompter.select_calls[0]
    assert all("(current)" not in c.label for c in choices)
