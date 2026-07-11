# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import argparse
import sys

import truststore

from .domain.plan import InstallationPlan
from .infrastructure.config_writer import TomlConfigWriter
from .infrastructure.existing_install import FileExistingInstallDetector
from .infrastructure.gpu import NvidiaSmiGPUDetector
from .infrastructure.health_checks import (
    OllamaHealthCheck,
    PgvectorExtensionHealthCheck,
    PostgresHealthCheck,
    ServerWebSocketHealthCheck,
)
from .infrastructure.model_installer import OllamaModelInstaller
from .infrastructure.prompter import QuestionaryPrompter
from .infrastructure.schema_runner import PsycopgSchemaRunner
from .infrastructure.toml_catalogue import TomlCatalogueRepository
from .services.errors import WizardAborted
from .services.ports import SchemaRunner
from .services.run_wizard import RunInstallWizard
from .services.steps import (
    CheckPrerequisites,
    DetectComputeDevice,
    GenerateConfig,
    ResolveSTTEngine,
    ResolveTTSEngines,
    RunHealthChecks,
    SelectLanguages,
    SelectLLM,
    SelectTopology,
    SetupSchema,
    ShowWelcome,
    WizardStep,
)


def _install_steps(
    catalogues: TomlCatalogueRepository,
    gpu: NvidiaSmiGPUDetector,
    installer: OllamaModelInstaller,
    writer: TomlConfigWriter,
    schema_runner: SchemaRunner,
) -> list[WizardStep]:
    # Both check lists are constructed here (not deferred to plan.database_url
    # at runtime) because no wizard step currently collects a real Postgres
    # connection string — plan.database_url is always its class default.
    # Revisit once a "collect Postgres connection" step exists (see
    # docs/PLAN.md) — the checks would need building after that step runs.
    default_database_url = InstallationPlan().database_url
    prerequisite_checks = [
        PostgresHealthCheck(default_database_url),
        PgvectorExtensionHealthCheck(default_database_url),
        OllamaHealthCheck(),
    ]
    health_checks = [
        PostgresHealthCheck(default_database_url),
        OllamaHealthCheck(),
        ServerWebSocketHealthCheck(),
    ]
    return [
        ShowWelcome(),
        SelectTopology(),
        CheckPrerequisites(prerequisite_checks),
        DetectComputeDevice(gpu),
        SelectLLM(catalogues, gpu, installer),
        SelectLanguages(catalogues),
        ResolveSTTEngine(catalogues, gpu, installer),
        ResolveTTSEngines(catalogues, installer),
        GenerateConfig(writer),
        SetupSchema(schema_runner),
        RunHealthChecks(health_checks),
    ]


def main() -> None:
    # Patches ssl.SSLContext to verify against the OS's native trust store instead of
    # certifi's bundled CA list — fixes SSL verification failures behind corporate
    # TLS-inspecting proxies (the proxy's CA is typically already OS-trusted but not
    # in certifi), without weakening verification on machines that don't have one.
    truststore.inject_into_ssl()

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
    installer = OllamaModelInstaller()
    writer = TomlConfigWriter()
    schema_runner = PsycopgSchemaRunner()
    steps = _install_steps(catalogues, gpu, installer, writer, schema_runner)
    wizard = RunInstallWizard(steps, prompter, FileExistingInstallDetector())

    try:
        plan = wizard.run()
    except WizardAborted as exc:
        prompter.info(str(exc))
        sys.exit(1)

    prompter.info(f"Selected LLM: {plan.llm_model_id}")


if __name__ == "__main__":
    main()
