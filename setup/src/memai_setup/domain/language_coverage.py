# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from .model import LanguageCode, STTCatalogueEntry, TTSCatalogueEntry


def _covers(languages: frozenset[LanguageCode], code: LanguageCode) -> bool:
    return "*" in languages or code in languages


def offered_languages(
    stt_entries: tuple[STTCatalogueEntry, ...],
    tts_entries: tuple[TTSCatalogueEntry, ...],
) -> frozenset[LanguageCode]:
    """Languages the wizard can actually offer: covered by at least one
    installable STT engine (`has_adapter`) and at least one TTS engine.
    Pure domain service — no infrastructure dependency, works off whatever
    CatalogueRepository returns."""
    available_stt = [e for e in stt_entries if e.has_adapter]

    tts_languages: set[LanguageCode] = set()
    for entry in tts_entries:
        tts_languages.update(entry.languages)

    return frozenset(code for code in tts_languages if any(_covers(e.languages, code) for e in available_stt))
