# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

CONFIG_PATH = Path(user_config_dir("memai", appauthor=False)) / "memai.toml"


@dataclass(frozen=True)
class ServerConfig:
    ws_port: int
    log_dir: Path
    database_url: str
    stt_model_path: str
    stt_device: str
    stt_compute_type: str
    tts_device: str | None
    llm_model: str
    llm_ollama_host: str | None
    memory_merge_threshold: float
    memory_disambiguate_threshold: float


def load_config(path: Path = CONFIG_PATH) -> ServerConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"Server config not found at {path}. "
            "Copy server/config/memai.example.toml to that location and fill in your values, "
            "or run memai-setup to generate it automatically."
        )
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    server = raw.get("server", {})
    db = raw.get("database", {})
    stt = raw.get("stt", {})
    tts = raw.get("tts", {})
    llm = raw.get("llm", {})
    memory = raw.get("memory", {})

    return ServerConfig(
        ws_port=int(server.get("ws_port", 8765)),
        log_dir=Path(server.get("log_dir", "logs/sessions")),
        database_url=db.get("url", "postgresql://memai:changeme@localhost:5432/memai"),
        stt_model_path=str(Path(stt.get("model_path", "~/models/faster-whisper-small")).expanduser()),
        stt_device=stt.get("device", "cpu"),
        stt_compute_type=stt.get("compute_type", "int8"),
        # None when [tts] is absent — preserves Kokoro's own torch.cuda.is_available()
        # auto-detect for configs written before this setting existed.
        tts_device=tts.get("device"),
        llm_model=llm.get("model", "aya-expanse"),
        llm_ollama_host=llm.get("ollama_host") or None,
        memory_merge_threshold=float(memory.get("merge_threshold", 0.93)),
        memory_disambiguate_threshold=float(memory.get("disambiguate_threshold", 0.75)),
    )
