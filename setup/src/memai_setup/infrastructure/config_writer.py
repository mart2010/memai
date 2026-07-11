# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w

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
_DEFAULT_LOG_DIR = "logs/sessions"


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
        config["llm"] = {"model": plan.llm_model_id or "aya-expanse"}
        self._write(config)

    def write_client_config(self, plan: InstallationPlan) -> None:
        # Only called for single-host (see GenerateConfig) — split-host client
        # config is written by a separate `memai-setup --client` run on the
        # client machine, where ssh_host is actually knowable.
        config = self._read_existing()
        config.setdefault("server", {}).setdefault("ws_port", _DEFAULT_WS_PORT)
        self._write(config)
