# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Integration test against the real faster-whisper model. Requires a CUDA GPU and a
cached/downloadable model — skips gracefully otherwise (e.g. the dev laptop)."""
import os
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import resample_poly

from memai_server.infrastructure.stt import FasterWhisperSTTService

_MODEL_PATH = os.environ.get("MEMAI_TEST_WHISPER_MODEL_PATH", "small")
_DEVICE = os.environ.get("MEMAI_TEST_WHISPER_DEVICE", "cuda")
_SAMPLE_RATE = 16000


def _synthesize_reference_audio(text: str, wav_path: Path) -> bytes:
    """Uses espeak-ng — already a system dependency for Kokoro's non-English backend
    (see infrastructure/tts.py) — to generate real speech for a genuine STT round trip,
    instead of committing a binary audio fixture to the repo."""
    subprocess.run(["espeak-ng", "-w", str(wav_path), text], check=True, capture_output=True)
    with wave.open(str(wav_path), "rb") as w:
        raw = w.readframes(w.getnframes())
        native_rate = w.getframerate()
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if native_rate != _SAMPLE_RATE:
        audio = resample_poly(audio, _SAMPLE_RATE, native_rate)
    return audio.astype(np.int16).tobytes()


@pytest.fixture(scope="module")
def stt_service() -> FasterWhisperSTTService:
    try:
        return FasterWhisperSTTService(_MODEL_PATH, device=_DEVICE, compute_type="float16")
    except Exception as e:  # noqa: BLE001 — genuinely any failure here means "can't run this test here"
        pytest.skip(f"faster-whisper model unavailable ({_MODEL_PATH} on {_DEVICE}): {e}")


class TestFasterWhisperSTTService:
    def test_transcribes_real_speech(self, stt_service: FasterWhisperSTTService, tmp_path: Path) -> None:
        """Spec: FR-102, TR-952"""
        if shutil.which("espeak-ng") is None:
            pytest.skip("espeak-ng not installed — needed to synthesize reference audio for this test")

        audio = _synthesize_reference_audio("hello world, this is a test", tmp_path / "ref.wav")
        text, language = stt_service.transcribe(audio)

        assert "hello" in text.lower()
        assert language.code == "en"

    def test_transcribes_silence_without_crashing(self, stt_service: FasterWhisperSTTService) -> None:
        """Spec: FR-103"""
        silence = np.zeros(_SAMPLE_RATE, dtype=np.int16).tobytes()
        text, language = stt_service.transcribe(silence)
        assert isinstance(text, str)
        assert language.code
