import pytest

from memai_setup.domain.plan import InstallationPlan
from memai_setup.services.errors import WizardAborted
from memai_setup.services.steps import ConfigureDatabaseConnection

from tests.fakes.fakes import FakeConnectionVerifier, FakeWizardPrompter


def test_peer_auth_success_sets_plan_database_url():
    verifier = FakeConnectionVerifier(postgres_ok=True, pgvector_ok=True)
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["peer"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.database_url == "postgresql:///memai?user=memai"
    assert verifier.verified_urls == ["postgresql:///memai?user=memai"]


def test_peer_auth_failure_declined_raises_wizard_aborted():
    verifier = FakeConnectionVerifier(postgres_ok=False, postgres_message="role does not exist")
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["peer"], confirm_answers=[False])
    plan = InstallationPlan()

    with pytest.raises(WizardAborted):
        step.run(plan, prompter)


def test_peer_auth_failure_confirmed_continues_and_mentions_pg_ident():
    verifier = FakeConnectionVerifier(postgres_ok=False, postgres_message="role does not exist")
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["peer"], confirm_answers=[True])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.database_url == "postgresql:///memai?user=memai"
    # The confirm message should guide the user to the actual fix.
    assert any("pg_ident.conf" in m for m in prompter.confirm_messages)


def test_password_auth_builds_dsn_from_defaults():
    verifier = FakeConnectionVerifier(postgres_ok=True, pgvector_ok=True)
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["password"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.database_url == "postgresql://memai:@localhost:5432/memai"


def test_pgvector_missing_warns_but_does_not_raise():
    verifier = FakeConnectionVerifier(postgres_ok=True, pgvector_ok=False, pgvector_message="not installed")
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["peer"])
    plan = InstallationPlan()

    step.run(plan, prompter)  # must not raise

    assert plan.database_url == "postgresql:///memai?user=memai"
    assert any("not installed" in m for m in prompter.info_messages)
