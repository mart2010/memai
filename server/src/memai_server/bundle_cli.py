# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""memai-bundle — one-shot persona-bundle installer (`memai-bundle install <path>`).

Runs as its own process with the same memai.toml the server uses: it needs the DB,
the embedding model, and Ollama (merge disambiguation + synthesis). The session loop
never calls or knows about this entrypoint.

Caveat (documented, not locked — single-user reality): run while the memai server is
IDLE. A concurrent consolidation run could race the same persona's upserts.
"""
import argparse
import sys
from pathlib import Path

import truststore

from .domain.model import resolve_installed_languages
from .infrastructure import postgres
from .infrastructure.bundle_toml import TomlPersonaBundleSource
from .infrastructure.config import load_config
from .infrastructure.embedding import SentenceTransformerEmbeddingService
from .infrastructure.llm.ollama import OllamaDisambiguationEvaluator, OllamaMemorySynthesizer
from .infrastructure.postgres import (
    PSBundleInstallLog,
    PSMemoryRepository,
    PSPersonaRepository,
    PSUnitOfWork,
    PSUserRepository,
)
from .infrastructure.tts import KOKORO_DEFAULT_VOICES
from .services.bundle_install import BundleInstallError, InstallPersonaBundle
from .services.ports import BundleFormatError
from .services.upsert import MemoryUpserter


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="memai-bundle",
        description="Install a persona bundle (a directory with bundle.toml + lessons/). "
        "Run while the memai server is idle.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    install = subcommands.add_parser("install", help="Install a bundle directory into memai's memory")
    install.add_argument("path", type=Path, help="path to the bundle directory")
    args = parser.parse_args()
    sys.exit(_install(args.path))


def _install(path: Path) -> int:
    # Same OS-trust-store fix as memai-server/the wizard (TLS-inspecting proxy insurance
    # for any adapter that still touches the network, e.g. a not-yet-cached model).
    truststore.inject_into_ssl()
    cfg = load_config()

    print("Connecting to database…")
    conn = postgres.connect(cfg.database_url)
    print("Loading embedding model…")
    embedding_service = SentenceTransformerEmbeddingService()

    install_bundle = InstallPersonaBundle(
        bundle_source=TomlPersonaBundleSource(),
        persona_repo=PSPersonaRepository(conn),
        user_repo=PSUserRepository(conn),
        upserter=MemoryUpserter(
            PSMemoryRepository(conn),
            embedding_service,
            OllamaDisambiguationEvaluator(model=cfg.llm_model, host=cfg.llm_ollama_host),
            OllamaMemorySynthesizer(model=cfg.llm_model, host=cfg.llm_ollama_host),
            cfg.memory_merge_threshold,
            cfg.memory_disambiguate_threshold,
        ),
        unit_of_work=PSUnitOfWork(conn),
        install_log=PSBundleInstallLog(conn),
        # Same native-teacher derivation as onboarding (see server.py's language_selected
        # handler) — used only when the bundle's [persona.voices] omits "default".
        default_voice_for=lambda language: KOKORO_DEFAULT_VOICES.get(language.code, "af_heart"),
        # Same installed-languages resolution as the server's composition root (FR-705):
        # a bundle targeting a language with no TTS voice on this machine fails here.
        installed_languages=resolve_installed_languages(cfg.installed_languages),
    )

    print(f"Installing bundle from {path}…")
    try:
        result = install_bundle.execute(path)
    except (BundleFormatError, BundleInstallError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1

    for notice in result.notices:
        print(f"Note: {notice}")
    action = "Created new persona" if result.persona_created else "Attached content to existing persona"
    print(f"{action} (id {result.persona_id}).")
    print(f"Items: {result.items_inserted} inserted, {result.items_merged} merged.")
    return 0


if __name__ == "__main__":
    main()
