# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from math import gcd

import numpy as np
from scipy.signal import resample_poly

_KOKORO_SAMPLE_RATE = 24_000
_CLIENT_SAMPLE_RATE = 16_000
_g = gcd(_KOKORO_SAMPLE_RATE, _CLIENT_SAMPLE_RATE)  # 8000
_RESAMPLE_UP = _CLIENT_SAMPLE_RATE // _g    # 2
_RESAMPLE_DOWN = _KOKORO_SAMPLE_RATE // _g  # 3

# Maps Kokoro voice prefix to the lang_code argument KPipeline expects.
# Prefix is the first character of the voice name (e.g. "af_heart" → "a").
# No Korean entry: the installed Kokoro package (hexgrad/Kokoro-82M) has no Korean
# pipeline at all — confirmed live 2026-07-08 (KPipeline(lang_code='ko') raises; its
# only valid codes are a/b/e/f/h/i/p/j/z). "ko" was removed from SUPPORTED_LANGUAGES
# for the same reason — see docs/PLAN.md Phase 3 findings.
_PREFIX_TO_LANG: dict[str, str] = {
    "a": "a",   # American English
    "b": "b",   # British English
    "e": "e",   # Spanish
    "f": "f",   # French
    "i": "i",   # Italian
    "j": "j",   # Japanese
    "p": "p",   # Portuguese
    "z": "z",   # Mandarin Chinese
}

# Default Kokoro voice per language code (IETF tag used in Language.code).
# Verify names against the installed kokoro package — they ship with each release.
KOKORO_DEFAULT_VOICES: dict[str, str] = {
    "en":    "af_heart",
    "fr":    "ff_siwis",
    "es":    "ef_dora",
    "it":    "if_sara",
    "pt":    "pf_dora",
    "ja":    "jf_alpha",
    "zh-cn": "zf_xiaobei",
}


class KokoroTTSService:
    """Kokoro TTS adapter.

    Lazily initialises one KPipeline per language prefix and resamples output
    from Kokoro's native 24 kHz to the client's expected 16 kHz.
    """

    def __init__(self, device: str | None = None) -> None:
        self._pipelines: dict[str, object] = {}
        self._device = device

    def _pipeline(self, voice: str):
        prefix = voice[0]
        if prefix not in self._pipelines:
            from kokoro import KPipeline
            lang_code = _PREFIX_TO_LANG.get(prefix, prefix)
            self._pipelines[prefix] = KPipeline(lang_code=lang_code, device=self._device)
        return self._pipelines[prefix]

    def synthesise(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        pipeline = self._pipeline(voice)
        chunks = [audio for _, _, audio in pipeline(text, voice=voice, speed=speed)]
        if not chunks:
            return b""
        combined = np.concatenate(chunks)
        resampled = resample_poly(combined, _RESAMPLE_UP, _RESAMPLE_DOWN).astype(np.float32)
        return resampled.tobytes()
