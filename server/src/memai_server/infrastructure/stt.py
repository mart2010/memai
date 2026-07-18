# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import numpy as np
from faster_whisper import WhisperModel

from ..domain.model import Language

# Whisper's own confidence that a segment isn't speech at all. Segments at or above this
# are dropped as hallucination-on-silence/noise (e.g. "Thank you for watching!" from a
# quiet room) rather than real utterances — FR-103 only guards an empty transcript, not
# a confidently-wrong non-empty one, so this filter is what actually makes that guard
# effective in practice.
_NO_SPEECH_THRESHOLD = 0.6


class FasterWhisperSTTService:
    """Adapter for faster-whisper. Language is always auto-detected; language_hint is unused."""

    def __init__(
        self,
        model_path: str,
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self._model = WhisperModel(model_path, device=device, compute_type=compute_type)

    def transcribe(self, audio: bytes) -> tuple[str, Language]:
        audio_array = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        # vad_filter (Silero VAD, bundled with faster-whisper) drops non-speech stretches
        # before decoding — the first line of defence against hallucinating text out of
        # silence. no_speech_prob below is the second: VAD can still let a short
        # noise/breath blip through, and Whisper can still decode a plausible-sounding
        # stock phrase for it.
        segments, info = self._model.transcribe(audio_array, beam_size=5, vad_filter=True)
        text = " ".join(s.text for s in segments if s.no_speech_prob < _NO_SPEECH_THRESHOLD).strip()
        return text, Language(info.language)
