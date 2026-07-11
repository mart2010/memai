from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.steps import DownloadEmbeddingModel

from tests.fakes.fakes import FakeModelInstaller, FakeWizardPrompter


def test_downloads_the_embedding_model():
    installer = FakeModelInstaller()
    step = DownloadEmbeddingModel(installer)
    plan = InstallationPlan()
    prompter = FakeWizardPrompter()

    step.run(plan, prompter)

    assert installer.downloaded_embedding_models == 1


def test_download_failure_warns_but_does_not_raise():
    installer = FakeModelInstaller(fail_download_embedding_model=True)
    step = DownloadEmbeddingModel(installer)
    plan = InstallationPlan()
    prompter = FakeWizardPrompter()

    step.run(plan, prompter)  # must not raise

    assert installer.downloaded_embedding_models == 0
    assert any("Could not pre-download the embedding model" in m for m in prompter.info_messages)
