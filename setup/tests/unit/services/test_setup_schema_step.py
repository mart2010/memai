from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.steps import SetupSchema

from tests.fakes.fakes import FakeSchemaRunner, FakeWizardPrompter


def test_applies_schema_using_plans_database_url():
    schema_runner = FakeSchemaRunner()
    step = SetupSchema(schema_runner)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert schema_runner.applied_to == [plan.database_url]
