# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import tomllib
from importlib import resources

from ..domain.model import (
    LLMCatalogueEntry,
    STTCatalogueEntry,
    TTSCatalogueEntry,
    TTSVoiceEntry,
    VRAMEstimate,
    WhisperModelEntry,
)


def _load(filename: str) -> dict:
    path = resources.files("memai_setup.catalogues") / filename
    with path.open("rb") as f:
        return tomllib.load(f)


class TomlCatalogueRepository:
    """Reads the TOML files shipped inside the package (memai_setup/catalogues/)."""

    def load_llm_catalogue(self) -> tuple[LLMCatalogueEntry, ...]:
        raw = _load("llm_catalogue.toml")
        return tuple(
            LLMCatalogueEntry(
                model_id=m["model_id"],
                display_name=m["display_name"],
                vram=VRAMEstimate(m["min_vram_gb"], m["recommended_vram_gb"]),
                languages=frozenset(m["languages"]),
                recommended=m["recommended"],
                reasoning=m["reasoning"],
                description=m["description"],
            )
            for m in raw["models"]
        )

    def load_stt_catalogue(self) -> tuple[STTCatalogueEntry, ...]:
        raw = _load("stt_catalogue.toml")
        models = tuple(
            WhisperModelEntry(m["name"], VRAMEstimate(m["min_vram_gb"], m["recommended_vram_gb"]))
            for m in raw["whisper_models"]
        )
        return tuple(
            STTCatalogueEntry(engine=e["name"], models=models, languages=frozenset(e["languages"]))
            for e in raw["engines"]
        )

    def load_tts_catalogue(self) -> tuple[TTSCatalogueEntry, ...]:
        raw = _load("tts_catalogue.toml")
        voices_by_engine: dict[str, list[TTSVoiceEntry]] = {}
        for v in raw.get("voices", []):
            voices_by_engine.setdefault(v["engine"], []).append(
                TTSVoiceEntry(v["voice_id"], v["language"], v["display_name"])
            )
        return tuple(
            TTSCatalogueEntry(
                engine=e["name"],
                licence=e["licence"],
                languages=frozenset(e["languages"]),
                voices=tuple(voices_by_engine.get(e["name"], [])),
                description=e["description"],
            )
            for e in raw["engines"]
        )
