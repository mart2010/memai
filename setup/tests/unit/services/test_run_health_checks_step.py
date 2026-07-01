from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.steps import RunHealthChecks

from tests.fakes.fakes import FakeHealthCheck, FakeWizardPrompter


def test_reports_ok_and_failed_checks():
    checks = [
        FakeHealthCheck("Postgres", ok=True, message="reachable"),
        FakeHealthCheck("Ollama", ok=False, message="connection refused"),
    ]
    step = RunHealthChecks(checks)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert any("[OK] Postgres: reachable" in m for m in prompter.info_messages)
    assert any("[FAILED] Ollama: connection refused" in m for m in prompter.info_messages)
