from memai_setup.domain.plan import InstallationPlan, Topology
from memai_setup.services.run_wizard import RunInstallWizard

from tests.fakes.fakes import FakeExistingInstallDetector, FakeWizardPrompter


class _RecordingStep:
    def __init__(self) -> None:
        self.plans: list[InstallationPlan] = []

    def run(self, plan, prompter) -> None:
        self.plans.append(plan)


def test_fresh_run_shows_no_current_state_banner():
    step = _RecordingStep()
    prompter = FakeWizardPrompter()
    wizard = RunInstallWizard([step], prompter, FakeExistingInstallDetector(None))

    plan = wizard.run()

    assert plan.from_existing_install is False
    assert prompter.headings == []
    assert step.plans == [plan]


def test_rerun_shows_current_state_and_passes_prefilled_plan_to_steps():
    """FR-706 — the recorded state is shown up front and every step starts
    from the pre-filled plan."""
    existing = InstallationPlan(
        from_existing_install=True,
        llm_model_id="aya-expanse",
        languages=["en", "fr"],
        whisper_model="small",
        database_url="postgresql://memai:s3cret@localhost:5432/memai",
    )
    step = _RecordingStep()
    prompter = FakeWizardPrompter()
    wizard = RunInstallWizard([step], prompter, FakeExistingInstallDetector(existing))

    plan = wizard.run()

    assert plan is existing
    assert step.plans == [existing]
    title, lines = prompter.headings[0]
    assert "Existing installation" in title
    body = "\n".join(lines)
    assert "aya-expanse" in body
    assert "English (en), French (fr)" in body
    assert "small" in body
    assert "s3cret" not in body  # password never echoed
    assert "***" in body


def test_rerun_locks_prefilled_topology():
    existing = InstallationPlan(from_existing_install=True)
    existing.set_topology(Topology.SPLIT_HOST)
    wizard = RunInstallWizard([], FakeWizardPrompter(), FakeExistingInstallDetector(existing))

    plan = wizard.run()

    try:
        plan.set_topology(Topology.SINGLE_HOST)
        raised = False
    except ValueError:
        raised = True
    assert raised
