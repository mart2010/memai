# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import argparse

from .infrastructure.existing_install import FileExistingInstallDetector
from .infrastructure.gpu import NvidiaSmiGPUDetector
from .infrastructure.prompter import QuestionaryPrompter
from .infrastructure.toml_catalogue import TomlCatalogueRepository
from .services.run_wizard import RunInstallWizard
from .services.steps import SelectLLM, SelectTopology, WizardStep


def _install_steps(catalogues: TomlCatalogueRepository, gpu: NvidiaSmiGPUDetector) -> list[WizardStep]:
    return [
        SelectTopology(),
        SelectLLM(catalogues, gpu),
        # TODO (see services/steps.py): SelectLanguages, ResolveSTTEngine,
        # ResolveTTSEngines, GenerateConfig, SetupSchema, RunHealthChecks
    ]


def main() -> None:
    parser = argparse.ArgumentParser(prog="memai-setup", description="Mémai installation wizard")
    parser.add_argument("--client", action="store_true", help="Configure this machine as a client only")
    parser.add_argument("--uninstall", action="store_true", help="Remove Mémai config and downloaded voice files")
    args = parser.parse_args()

    if args.uninstall:
        raise NotImplementedError("TODO: uninstall flow — remove platformdirs config + downloaded Piper voices")
    if args.client:
        raise NotImplementedError("TODO: --client flow — SSH tunnel + WebSocket health check only")

    prompter = QuestionaryPrompter()
    catalogues = TomlCatalogueRepository()
    gpu = NvidiaSmiGPUDetector()
    wizard = RunInstallWizard(_install_steps(catalogues, gpu), prompter, FileExistingInstallDetector())

    plan = wizard.run()
    prompter.info(f"Selected LLM: {plan.llm_model_id}")


if __name__ == "__main__":
    main()
