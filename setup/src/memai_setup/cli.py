# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import argparse
import sys

import truststore

from .infrastructure.config_writer import TomlConfigWriter
from .infrastructure.existing_install import FileExistingInstallDetector
from .infrastructure.gpu import SystemGPUDetector
from .infrastructure.health_checks import OllamaHealthCheck, PsycopgConnectionVerifier
from .infrastructure.model_installer import OllamaModelInstaller
from .infrastructure.prompter import QuestionaryPrompter
from .infrastructure.schema_runner import PsycopgSchemaRunner
from .infrastructure.toml_catalogue import TomlCatalogueRepository
from .services.errors import WizardAborted
from .services.ports import DatabaseConnectionVerifier, SchemaRunner
from .services.run_wizard import RunInstallWizard
from .services.steps import (
    CheckPrerequisites,
    ConfigureDatabaseConnection,
    DetectComputeDevice,
    DownloadEmbeddingModel,
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
    gpu: SystemGPUDetector,
    installer: OllamaModelInstaller,
    writer: TomlConfigWriter,
    schema_runner: SchemaRunner,
    verifier: DatabaseConnectionVerifier,
) -> list[WizardStep]:
    # Postgres/pgvector are verified by ConfigureDatabaseConnection itself
    # (using the connection it just collected), not listed here — unlike
    # Ollama, there's no fixed database_url to build a check from until that
    # step has run. SetupSchema/RunHealthChecks run after it, so plan.database_url
    # is always real by the time anything else needs it.
    prerequisite_checks = [OllamaHealthCheck()]
    # No ServerWebSocketHealthCheck here — the wizard never starts memai-server
    # itself (see its docstring), so right after a fresh install this would
    # always fail with "connection refused," which reads as a wizard error
    # rather than the expected "you haven't started it yet." main() tells the
    # user how to start it instead, once the wizard actually succeeds.
    health_checks = [OllamaHealthCheck()]
    return [
        ShowWelcome(),
        SelectTopology(),
        ConfigureDatabaseConnection(verifier),
        CheckPrerequisites(prerequisite_checks),
        DetectComputeDevice(gpu),
        SelectLLM(catalogues, gpu, installer),
        SelectLanguages(catalogues),
        ResolveSTTEngine(catalogues, gpu, installer),
        ResolveTTSEngines(catalogues, installer),
        DownloadEmbeddingModel(installer),
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
    gpu = SystemGPUDetector()
    installer = OllamaModelInstaller()
    writer = TomlConfigWriter()
    schema_runner = PsycopgSchemaRunner()
    verifier = PsycopgConnectionVerifier()
    steps = _install_steps(catalogues, gpu, installer, writer, schema_runner, verifier)
    wizard = RunInstallWizard(steps, prompter, FileExistingInstallDetector())

    try:
        plan = wizard.run()
    except WizardAborted as exc:
        prompter.info(str(exc))
        sys.exit(1)

    start_cmd = r".venv\Scripts\memai-server" if sys.platform.startswith("win") else ".venv/bin/memai-server"
    prompter.heading(
        "Mémai setup complete!",
        [
            f"Selected LLM: {plan.llm_model_id}",
            "Start the server with:",
            f"  cd server && {start_cmd}",
        ],
    )


if __name__ == "__main__":
    main()
