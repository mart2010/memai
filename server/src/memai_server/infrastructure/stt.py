# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import numpy as np
from faster_whisper import WhisperModel

from ..domain.model import Language


class FasterWhisperSTTService:
    """Adapter for faster-whisper. Language is always auto-detected; language_hint is unused."""

    def __init__(
        self,
        model_path: str,
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self._model = WhisperModel(model_path, device=device, compute_type=compute_type)

    def transcribe(self, audio: bytes, language_hint: Language | None) -> tuple[str, Language]:
        audio_array = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        segments, info = self._model.transcribe(audio_array, beam_size=5)
        text = " ".join(s.text for s in segments).strip()
        return text, Language(info.language)
