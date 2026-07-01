import pytest

from memai_setup.domain.plan import InstallationPlan, Topology


def test_locked_topology_rejects_change():
    plan = InstallationPlan(topology=Topology.SINGLE_HOST)
    plan.lock_topology()

    with pytest.raises(ValueError):
        plan.set_topology(Topology.SPLIT_HOST)


def test_locked_topology_allows_setting_same_value():
    plan = InstallationPlan(topology=Topology.SINGLE_HOST)
    plan.lock_topology()

    plan.set_topology(Topology.SINGLE_HOST)

    assert plan.topology == Topology.SINGLE_HOST


def test_unlocked_topology_can_change_freely():
    plan = InstallationPlan(topology=Topology.SINGLE_HOST)

    plan.set_topology(Topology.SPLIT_HOST)

    assert plan.topology == Topology.SPLIT_HOST


def test_cannot_lock_before_topology_is_set():
    plan = InstallationPlan()

    with pytest.raises(ValueError):
        plan.lock_topology()
