import pytest

from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.errors import WizardAborted
from memai_setup.services.steps import CheckPrerequisites

from tests.fakes.fakes import FakeHealthCheck, FakeWizardPrompter


def test_all_passing_does_not_prompt():
    checks = [FakeHealthCheck("Postgres", ok=True, message="reachable")]
    step = CheckPrerequisites(checks)
    prompter = FakeWizardPrompter()  # no confirm_answers scripted — would raise IndexError if consulted
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert any("[OK] Postgres: reachable" in m for m in prompter.info_messages)


def test_failure_with_confirmation_continues():
    checks = [FakeHealthCheck("Ollama", ok=False, message="connection refused")]
    step = CheckPrerequisites(checks)
    prompter = FakeWizardPrompter(confirm_answers=[True])
    plan = InstallationPlan()

    step.run(plan, prompter)  # should not raise

    assert any("[FAILED] Ollama: connection refused" in m for m in prompter.info_messages)


def test_failure_without_confirmation_aborts():
    checks = [FakeHealthCheck("Ollama", ok=False, message="connection refused")]
    step = CheckPrerequisites(checks)
    prompter = FakeWizardPrompter(confirm_answers=[False])
    plan = InstallationPlan()

    with pytest.raises(WizardAborted):
        step.run(plan, prompter)
