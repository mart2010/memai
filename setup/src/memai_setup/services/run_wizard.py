# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from ..domain.plan import InstallationPlan
from .ports import ExistingInstallDetector, WizardPrompter
from .steps import WizardStep


class RunInstallWizard:
    """Orchestrates an ordered sequence of WizardSteps against one shared
    InstallationPlan. Pre-fills + locks topology from a prior install when
    ExistingInstallDetector finds one (re-run support)."""

    def __init__(
        self,
        steps: list[WizardStep],
        prompter: WizardPrompter,
        existing_install: ExistingInstallDetector,
    ) -> None:
        self._steps = steps
        self._prompter = prompter
        self._existing_install = existing_install

    def run(self) -> InstallationPlan:
        plan = self._existing_install.load_existing_plan() or InstallationPlan()
        if plan.topology is not None:
            plan.lock_topology()
        for step in self._steps:
            step.run(plan, self._prompter)
        return plan
