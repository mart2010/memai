from memai_setup.domain.plan import InstallationPlan, Topology
from memai_setup.services.steps import GenerateConfig

from tests.fakes.fakes import FakeConfigWriter, FakeWizardPrompter


def test_single_host_writes_both_server_and_client_config():
    writer = FakeConfigWriter()
    step = GenerateConfig(writer)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan(topology=Topology.SINGLE_HOST)

    step.run(plan, prompter)

    assert writer.server_config_writes == [plan]
    assert writer.client_config_writes == [plan]


def test_split_host_only_writes_server_config_and_informs_user():
    writer = FakeConfigWriter()
    step = GenerateConfig(writer)
    prompter = FakeWizardPrompter()
    plan = InstallationPlan(topology=Topology.SPLIT_HOST)

    step.run(plan, prompter)

    assert writer.server_config_writes == [plan]
    assert writer.client_config_writes == []
    assert any("memai-setup --client" in m for m in prompter.info_messages)
