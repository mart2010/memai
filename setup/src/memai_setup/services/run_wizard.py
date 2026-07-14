# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from ..domain.languages import format_language
from ..domain.plan import InstallationPlan, masked_database_url
from .ports import ExistingInstallDetector, WizardPrompter
from .steps import WizardStep


def _current_state_lines(plan: InstallationPlan) -> list[str]:
    languages = ", ".join(format_language(code) for code in plan.languages) or "(none recorded)"
    return [
        "Re-running keeps this state unless you change an answer below —",
        "installed languages come pre-checked, current choices are the defaults.",
        "",
        f"  LLM model:       {plan.llm_model_id or '(not set)'}",
        f"  Languages:       {languages}",
        f"  Whisper model:   {plan.whisper_model or '(not set)'}",
        f"  Compute device:  {plan.compute_device}",
        f"  Database:        {masked_database_url(plan.database_url)}",
        f"  Topology:        {plan.topology.name.lower() if plan.topology else 'asked again below'}",
    ]


class RunInstallWizard:
    """Orchestrates an ordered sequence of WizardSteps against one shared
    InstallationPlan. Pre-fills + locks topology from a prior install when
    ExistingInstallDetector finds one (re-run support, FR-706): the recorded
    state is shown up front and every step starts from it."""

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
        if plan.from_existing_install:
            self._prompter.heading("Existing installation detected", _current_state_lines(plan))
        if plan.topology is not None:
            plan.lock_topology()
        for step in self._steps:
            step.run(plan, self._prompter)
        return plan
