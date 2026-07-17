import pytest

from memai_setup.domain.plan import InstallationPlan
from memai_setup.services import steps as steps_module
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


def test_rerun_offers_keep_current_connection_as_default():
    """FR-706 — re-run pre-fill: the recorded DSN is offered first (password
    masked in the label), is the default, and is still verified when kept."""
    verifier = FakeConnectionVerifier(postgres_ok=True, pgvector_ok=True)
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["keep"])
    plan = InstallationPlan(
        database_url="postgresql://memai:s3cret@dbhost:5432/memai", from_existing_install=True
    )

    step.run(plan, prompter)

    _, choices, default = prompter.select_calls[0]
    assert choices[0].value == "keep"
    assert "s3cret" not in choices[0].label
    assert "***" in choices[0].label
    assert default == "keep"
    assert plan.database_url == "postgresql://memai:s3cret@dbhost:5432/memai"
    assert verifier.verified_urls == ["postgresql://memai:s3cret@dbhost:5432/memai"]


def test_fresh_run_has_no_keep_option():
    verifier = FakeConnectionVerifier(postgres_ok=True, pgvector_ok=True)
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["peer"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    _, choices, default = prompter.select_calls[0]
    assert all(c.value != "keep" for c in choices)
    assert default is None


def test_windows_offers_sspi_not_peer(monkeypatch):
    monkeypatch.setattr(steps_module.sys, "platform", "win32")
    verifier = FakeConnectionVerifier(postgres_ok=True, pgvector_ok=True)
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["sspi"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    _, choices, _ = prompter.select_calls[0]
    assert [c.value for c in choices] == ["sspi", "password"]
    assert plan.database_url == "postgresql://memai@localhost:5432/memai"
    assert verifier.verified_urls == ["postgresql://memai@localhost:5432/memai"]


def test_non_windows_offers_peer_not_sspi(monkeypatch):
    monkeypatch.setattr(steps_module.sys, "platform", "linux")
    verifier = FakeConnectionVerifier(postgres_ok=True, pgvector_ok=True)
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["peer"])
    plan = InstallationPlan()

    step.run(plan, prompter)

    _, choices, _ = prompter.select_calls[0]
    assert [c.value for c in choices] == ["peer", "password"]


def test_sspi_auth_failure_confirmed_continues_and_mentions_pg_ident(monkeypatch):
    monkeypatch.setattr(steps_module.sys, "platform", "win32")
    verifier = FakeConnectionVerifier(postgres_ok=False, postgres_message="role does not exist")
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["sspi"], confirm_answers=[True])
    plan = InstallationPlan()

    step.run(plan, prompter)

    assert plan.database_url == "postgresql://memai@localhost:5432/memai"
    assert any("pg_ident.conf" in m and "sspi" in m for m in prompter.confirm_messages)


def test_rerun_keep_sspi_connection_still_shows_sspi_hint_on_failure(monkeypatch):
    monkeypatch.setattr(steps_module.sys, "platform", "win32")
    verifier = FakeConnectionVerifier(postgres_ok=False, postgres_message="role does not exist")
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["keep"], confirm_answers=[True])
    plan = InstallationPlan(
        database_url="postgresql://memai@localhost:5432/memai", from_existing_install=True
    )

    step.run(plan, prompter)

    assert any("pg_ident.conf" in m and "sspi" in m for m in prompter.confirm_messages)


def test_pgvector_missing_warns_but_does_not_raise():
    verifier = FakeConnectionVerifier(postgres_ok=True, pgvector_ok=False, pgvector_message="not installed")
    step = ConfigureDatabaseConnection(verifier)
    prompter = FakeWizardPrompter(select_answers=["peer"])
    plan = InstallationPlan()

    step.run(plan, prompter)  # must not raise

    assert plan.database_url == "postgresql:///memai?user=memai"
    assert any("not installed" in m for m in prompter.info_messages)
