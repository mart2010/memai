from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.steps import ShowWelcome

from tests.fakes.fakes import FakeWizardPrompter


def test_renders_as_one_heading_not_plain_info_lines():
    step = ShowWelcome()
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert len(prompter.headings) == 1
    assert prompter.info_messages == []  # everything goes through heading(), not info()


def test_explains_both_topologies_before_the_ssh_prerequisite():
    step = ShowWelcome()
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    _title, lines = prompter.headings[0]
    joined = "\n".join(lines).lower()
    assert "single-host" in joined
    assert "split-host" in joined
    ssh_prerequisite_line = next(line for line in lines if "key auth" in line.lower())
    assert "split-host" in ssh_prerequisite_line.lower()


def test_portaudio_prerequisite_is_scoped_to_macos_linux():
    step = ShowWelcome()
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    _title, lines = prompter.headings[0]
    portaudio_line = next(line for line in lines if "portaudio" in line.lower())
    assert "macos" in portaudio_line.lower() and "linux" in portaudio_line.lower()
    assert "windows" in portaudio_line.lower()


def test_lists_all_other_prerequisites():
    step = ShowWelcome()
    prompter = FakeWizardPrompter()
    plan = InstallationPlan()

    step.run(plan, prompter)

    joined = "\n".join(prompter.headings[0][1])
    assert "pgvector" in joined
    assert "Ollama" in joined
    assert "CUDA" in joined
