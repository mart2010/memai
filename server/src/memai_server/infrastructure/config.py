# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import tomllib
import tomli_w
from dataclasses import dataclass
from pathlib import Path

from ..domain.model import Language


@dataclass(frozen=True)
class ServerConfig:
    ws_port: int
    log_dir: Path
    stt_model_path: str
    stt_device: str
    stt_compute_type: str
    llm_model: str
    llm_ollama_host: str | None
    primary_language: Language | None


def load_config(path: Path) -> ServerConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    server = raw.get("server", {})
    stt = raw.get("stt", {})
    llm = raw.get("llm", {})
    vc = raw.get("voice_configurable", {})

    raw_lang = vc.get("primary_language")

    return ServerConfig(
        ws_port=int(server.get("ws_port", 8765)),
        log_dir=Path(server.get("log_dir", "logs/sessions")),
        stt_model_path=str(Path(stt.get("model_path", "~/models/faster-whisper-small")).expanduser()),
        stt_device=stt.get("device", "cuda"),
        stt_compute_type=stt.get("compute_type", "float16"),
        llm_model=llm.get("model", "aya-expanse"),
        llm_ollama_host=llm.get("ollama_host") or None,
        primary_language=Language(raw_lang) if raw_lang else None,
    )


def update_voice_config(path: Path, key: str, value: str) -> None:
    """Write a single key into [voice_configurable] and save back to disk."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    raw.setdefault("voice_configurable", {})[key] = value
    with open(path, "wb") as f:
        tomli_w.dump(raw, f)
