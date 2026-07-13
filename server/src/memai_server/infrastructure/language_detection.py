# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import re

from py3langid.langid import LanguageIdentifier, MODEL_FILE

# Below this many alphabetic characters, a language guess isn't trusted regardless of
# what the classifier says — short interjections ("Ciao!", "No.", "Sì.") are genuinely
# ambiguous to statistical language ID (some are borrowed/international words in the
# first place). Placeholder pending real tuning against live turns, same posture as the
# 0.93/0.75 upsert thresholds and the prefix-scan window.
_MIN_CONFIDENT_CHARS = 8

_NON_WORD = re.compile(r"[^\w]", re.UNICODE)


class Py3LangidLanguageDetector:
    """Restricts each call to the given `candidates` (an instance-scoped identifier,
    not the module-level global — safe under this project's single-connection,
    single-turn-at-a-time model, see INV-3). No network/model download: py3langid
    ships its compact model inline, unlike the STT/TTS/embedding adapters."""

    def __init__(self) -> None:
        self._identifier = LanguageIdentifier.from_pickled_model(MODEL_FILE)

    def detect(self, text: str, candidates: tuple[str, ...]) -> str | None:
        if len(_NON_WORD.sub("", text)) < _MIN_CONFIDENT_CHARS:
            return None
        self._identifier.set_languages(list(candidates))
        lang, _score = self._identifier.classify(text)
        return lang
