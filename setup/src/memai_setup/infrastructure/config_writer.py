# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w
from platformdirs import user_data_dir

from ..domain.plan import InstallationPlan
from .existing_install import CONFIG_PATH

# Server and client both read the SAME memai.toml path (see
# server/infrastructure/config.py and client/client.py — both compute
# `user_config_dir("memai")/memai.toml` independently). For single-host
# topology both processes run on this machine and share that one file, so
# write_server_config/write_client_config must merge into it rather than
# overwrite each other's sections — each reads its own keys out of the
# `[server]` table and ignores the rest (server: ws_port/log_dir, client:
# ws_port/ssh_host).
_DEFAULT_WS_PORT = 8765
# Matches server/infrastructure/config.py's own default — absolute and OS-independent
# (the platform data dir, not a cwd-relative literal, which used to scatter session
# logs depending on where the server process happened to be launched from).
_DEFAULT_LOG_DIR = str(Path(user_data_dir("memai", appauthor=False)) / "sessions")


class TomlConfigWriter:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self._path = path

    def _read_existing(self) -> dict:
        if not self._path.exists():
            return {}
        with open(self._path, "rb") as f:
            return tomllib.load(f)

    def _write(self, config: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "wb") as f:
            tomli_w.dump(config, f)
        # This file carries database.url (and, for TCP/password connections,
        # a plaintext credential) — tighten permissions on every write, not
        # just first creation, so a config written before this existed (or
        # under a looser umask) also gets corrected on the next wizard run.
        self._path.chmod(0o600)

    def write_server_config(self, plan: InstallationPlan) -> None:
        config = self._read_existing()
        config.setdefault("server", {}).update({"ws_port": _DEFAULT_WS_PORT, "log_dir": _DEFAULT_LOG_DIR})
        config["database"] = {"url": plan.database_url}
        device = plan.compute_device
        config["stt"] = {
            "model_path": plan.whisper_model or "small",
            "device": device,
            "compute_type": "float16" if device == "cuda" else "int8",
        }
        config["tts"] = {"device": device}
        # `model`/`ollama_host` are always the local Ollama model for the offline memory
        # pipeline (FR-707) — provider/base_url/remote_model/api_key are omitted entirely
        # for the common "ollama" case, keeping written configs minimal/unchanged from
        # before this setting existed; only present when live conversation is remote.
        llm_config = {"model": plan.llm_model_id or "aya-expanse"}
        if plan.llm_provider == "openai_compatible":
            llm_config["provider"] = plan.llm_provider
            llm_config["base_url"] = plan.llm_base_url or ""
            llm_config["remote_model"] = plan.llm_remote_model or ""
            if plan.llm_api_key:
                llm_config["api_key"] = plan.llm_api_key
        config["llm"] = llm_config
        # The wizard-selected languages ARE the installed-languages contract: the
        # server offers onboarding language selection only within this set (FR-705).
        # Absent (configs written before this key existed), the server falls back to
        # all of SUPPORTED_LANGUAGES.
        config["languages"] = {"installed": plan.languages}
        self._write(config)

    def write_client_config(self, plan: InstallationPlan) -> None:
        # Only called for single-host (see GenerateConfig) — split-host client
        # config is written by a separate `memai-setup --client` run on the
        # client machine, where ssh_host is actually knowable.
        config = self._read_existing()
        config.setdefault("server", {}).setdefault("ws_port", _DEFAULT_WS_PORT)
        self._write(config)
