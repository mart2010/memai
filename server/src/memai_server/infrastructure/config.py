# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

CONFIG_PATH = Path(user_config_dir("memai", appauthor=False)) / "memai.toml"
# Session logs are persistent app data, not settings (INV-5, kept forever) — the data
# dir, not the config dir. Absolute and OS-independent, unlike the old "logs/sessions"
# literal default, which silently resolved against whatever directory the server
# process happened to be launched from (a real bug: it scattered logs across
# directories depending on cwd, and broke session-tail continuity/crash-recovery replay
# across launches from different locations).
_DEFAULT_LOG_DIR = Path(user_data_dir("memai", appauthor=False)) / "sessions"


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
    # Live-conversation LLM backend (FR-707/TR-955). "ollama" (default) reuses
    # llm_model/llm_ollama_host above for the live path too, exactly as before this
    # setting existed — fully backward compatible with configs that predate it.
    # "openai_compatible" swaps only the main conversational LLM (see ProcessTurn) to
    # a remote HTTP endpoint; the offline pipeline (consolidation, MemoryBrief, tutor
    # strategy helpers) always stays on llm_model/llm_ollama_host, regardless of this
    # setting — a GPU-less/CPU-only offline run is fine, per design decision, just
    # slower. Recall gating (FR-309/TR-314) doesn't read this field at all anymore —
    # it's local threshold logic now, not an LLM call.
    llm_provider: str
    llm_base_url: str | None  # required when llm_provider == "openai_compatible"
    llm_remote_model: str | None  # required when llm_provider == "openai_compatible"
    llm_api_key: str | None  # optional even when llm_provider == "openai_compatible"
    memory_merge_threshold: float
    memory_disambiguate_threshold: float
    # How many of the previous session's last turns get injected as session tail
    # (FR-109), when that session ended recently enough to count as a continuation. 0
    # disables tail injection entirely — useful while testing, since a prior session's
    # content (e.g. a stray language drift) can otherwise bias a fresh session. Not
    # voice-configurable (FR-701 doesn't apply — this isn't a GA/persona setting);
    # same technical-tuning-knob posture as [memory]'s thresholds below.
    session_tail_turns: int = 10
    # Wizard-selected languages ([languages].installed, FR-705). Empty = key absent
    # (config written before it existed) — the composition root then treats every
    # SUPPORTED_LANGUAGES entry as installed.
    installed_languages: tuple[str, ...] = ()


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
    languages = raw.get("languages", {})

    llm_provider = llm.get("provider", "ollama")
    if llm_provider not in ("ollama", "openai_compatible"):
        raise RuntimeError(
            f"[llm].provider must be 'ollama' or 'openai_compatible', got {llm_provider!r}"
        )
    llm_base_url = llm.get("base_url") or None
    llm_remote_model = llm.get("remote_model") or None
    if llm_provider == "openai_compatible" and not (llm_base_url and llm_remote_model):
        raise RuntimeError(
            "[llm].provider = 'openai_compatible' requires both [llm].base_url and "
            "[llm].remote_model to be set"
        )

    return ServerConfig(
        ws_port=int(server.get("ws_port", 8765)),
        log_dir=Path(server.get("log_dir")) if server.get("log_dir") else _DEFAULT_LOG_DIR,
        database_url=db.get("url", "postgresql://memai:changeme@localhost:5432/memai"),
        stt_model_path=str(Path(stt.get("model_path", "~/models/faster-whisper-small")).expanduser()),
        stt_device=stt.get("device", "cpu"),
        stt_compute_type=stt.get("compute_type", "int8"),
        # None when [tts] is absent — preserves Kokoro's own torch.cuda.is_available()
        # auto-detect for configs written before this setting existed.
        tts_device=tts.get("device"),
        llm_model=llm.get("model", "aya-expanse"),
        llm_ollama_host=llm.get("ollama_host") or None,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        llm_remote_model=llm_remote_model,
        llm_api_key=llm.get("api_key") or None,
        memory_merge_threshold=float(memory.get("merge_threshold", 0.93)),
        memory_disambiguate_threshold=float(memory.get("disambiguate_threshold", 0.75)),
        session_tail_turns=int(server.get("session_tail_turns", 10)),
        installed_languages=tuple(languages.get("installed", [])),
    )
