# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from .model import LanguageCode

# ISO 639-1 -> English display name. Covers every code referenced by the
# catalogues (catalogues/*.toml). Extend this when a new language is added to
# any catalogue — a missing entry falls back to the bare code, it doesn't error.
LANGUAGE_NAMES: dict[LanguageCode, str] = {
    "ar": "Arabic",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "hi": "Hindi",
    "hu": "Hungarian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "ko": "Korean",
    "lb": "Luxembourgish",
    "lv": "Latvian",
    "ml": "Malayalam",
    "ne": "Nepali",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh": "Chinese",
}


def format_language(code: LanguageCode) -> str:
    """"English (en)" style label — plain-language wizard prompts per CLAUDE.md
    ("using plain-language explanations throughout"), never a bare ISO code."""
    return f"{LANGUAGE_NAMES.get(code, code)} ({code})"
