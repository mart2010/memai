# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Live LLM quality gate for the language-tutor's tag-emission design (persona switch,
lazy selection-batch injection, [FOCUS:] steering, two-teacher [SPEAKER:] cast).

**Not part of continuous testing.** Run manually, once, when evaluating whether a
candidate default LLM (a model swap, a prompt-pack change) meets the reliability bar
this design assumes — a report card for a design/model pairing, not a regression test.
`server/tests/unit/services/test_session.py` already covers the plumbing (parser,
splicing, selection wiring) deterministically via `FakeLLMService`; this exercises the
same behaviour against a real model's actual, non-deterministic output.

Real findings from this exact test (docs/PLAN.md Phase 12 live smoke + gemma3:27b
follow-up, 2026-07-13): `aya-expanse` and `gemma3:27b` both understand persona-switch/
focus intent semantically but routinely never emit the literal [PERSONA:]/[FOCUS:] tag
at all — they narrate the action in prose instead ("I am now switching to...") without
using the mechanism. **A FAILING run here is expected and meaningful for those models,
not a bug in this test** — that's the point of a quality gate.

Setup (all manual, on the machine running the real server):

1. Start the server with tracing enabled and its stdout captured to a file (tracing is
   opt-in and off by default — see `_TUTOR_DEBUG` in `services/session.py` — because
   some traced lines echo conversation content, unlike the always-on [latency] lines):

       MEMAI_TEST_TUTOR_DEBUG=1 .venv/bin/memai-server > /tmp/tutor_gate.log 2>&1 &

2. Make sure a language-tutor persona is installed (e.g. `bundles/italian-a0-starter`)
   and note its exact `AssistantPersona.name`.
3. Run, from `server/`:

       MEMAI_TEST_SERVER_LOG_PATH=/tmp/tutor_gate.log \\
       MEMAI_TEST_TUTOR_PERSONA_NAME="Tutor Italiano" \\
       uv run pytest tests/e2e -s

Drives the real WebSocket wire protocol exactly as `client/src/memai_client/client.py`
does (16kHz PCM16 binary frames in, float32 PCM16kHz binary frames out), using
espeak-ng-synthesized speech instead of a real mic/speakers. Skips gracefully when
`MEMAI_TEST_SERVER_LOG_PATH` isn't set, same posture as `tests/integration/conftest.py`
— so a bare `pytest` run, CI included, never picks this up by accident.
"""
import ast
import json
import os
import re
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
import websockets
from scipy.signal import resample_poly

_SAMPLE_RATE = 16000
_WS_URL = os.environ.get("MEMAI_TEST_SERVER_WS_URL", "ws://localhost:8765")
_LOG_PATH = os.environ.get("MEMAI_TEST_SERVER_LOG_PATH")
_TUTOR_PERSONA_NAME = os.environ.get("MEMAI_TEST_TUTOR_PERSONA_NAME", "Tutor Italiano")
_MAX_SWITCH_ATTEMPTS = int(os.environ.get("MEMAI_TEST_TUTOR_SWITCH_ATTEMPTS", "5"))

pytestmark = pytest.mark.skipif(
    not _LOG_PATH,
    reason="MEMAI_TEST_SERVER_LOG_PATH not set — this is a manual quality-gate run, "
    "see the module docstring for setup; not run as part of normal/CI testing",
)

_BATCH_RE = re.compile(r"\[tutor-debug\] fetched batch for [^:]+: (\[.*\])")
_VOICE_RE = re.compile(r"\[tutor-debug\] segment voice=(\S+)")
_FOCUS_RE = re.compile(r"\[tutor-debug\] focus=")


def _synthesize(text: str, tmp_path: Path, tag: str) -> bytes:
    """Real speech via espeak-ng — same approach as tests/integration/test_stt.py, no
    binary fixture committed, no mic/speakers needed."""
    wav_path = tmp_path / f"{tag}.wav"
    subprocess.run(["espeak-ng", "-w", str(wav_path), text], check=True, capture_output=True)
    with wave.open(str(wav_path), "rb") as w:
        raw = w.readframes(w.getnframes())
        native_rate = w.getframerate()
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if native_rate != _SAMPLE_RATE:
        audio = resample_poly(audio, _SAMPLE_RATE, native_rate)
    return audio.astype(np.int16).tobytes()


async def _say(ws, text: str, tmp_path: Path, tag: str) -> None:
    audio = _synthesize(text, tmp_path, tag)
    frame_bytes = int(_SAMPLE_RATE * 0.03) * 2
    for i in range(0, len(audio), frame_bytes):
        await ws.send(audio[i:i + frame_bytes])
    await ws.send(json.dumps({"type": "end_utterance"}))
    while True:
        msg = await ws.recv()
        if isinstance(msg, str) and json.loads(msg).get("type") == "speaking_end":
            break


def _log_tail(pos: int) -> tuple[str, int]:
    with open(_LOG_PATH) as f:
        f.seek(pos)
        return f.read(), f.tell()


@pytest.mark.asyncio
async def test_tutor_llm_quality_gate(tmp_path: Path) -> None:
    """Spec: FR-202 (persona switch), TR-306 (selection batch), FR-502 ([FOCUS:]),
    FR-205/TR-305 (two-teacher [SPEAKER:] cast) — live reliability against a real
    model's real output, not plumbing correctness (see test_session.py for that)."""
    results: dict[str, str] = {}

    async with websockets.connect(_WS_URL, max_size=None) as ws:
        pos = Path(_LOG_PATH).stat().st_size

        switched = False
        for attempt in range(1, _MAX_SWITCH_ATTEMPTS + 1):
            # First attempt uses the real phrase; later attempts confirm with a plain
            # "Yes" instead of repeating a foreign proper noun STT may keep mangling —
            # isolates model tag-emission reliability from synthetic-audio artifacts.
            text = f"Switch to {_TUTOR_PERSONA_NAME}." if attempt == 1 else "Yes, please switch me."
            await _say(ws, text, tmp_path, f"switch{attempt}")
            new_log, pos = _log_tail(pos)
            if f"persona switched to {_TUTOR_PERSONA_NAME!r}" in new_log:
                switched = True
                results["persona_switch"] = f"PASS (confirmed on attempt {attempt}/{_MAX_SWITCH_ATTEMPTS})"
                break
        if not switched:
            results["persona_switch"] = f"FAIL (not confirmed within {_MAX_SWITCH_ATTEMPTS} attempts)"

        if not switched:
            results["selection_batch"] = "SKIP (persona switch prerequisite failed)"
            results["cast_voice_switch"] = "SKIP (persona switch prerequisite failed)"
            results["focus_marker"] = "SKIP (persona switch prerequisite failed)"
        else:
            await _say(ws, "Ciao! I'm ready for today's lesson.", tmp_path, "lesson")
            new_log, pos = _log_tail(pos)
            batch_match = _BATCH_RE.search(new_log)
            batch = ast.literal_eval(batch_match.group(1)) if batch_match else []
            results["selection_batch"] = f"PASS (items: {batch})" if batch else "FAIL (no batch injected)"

            voices = set(_VOICE_RE.findall(new_log))
            results["cast_voice_switch"] = (
                f"PASS (voices used: {voices})" if len(voices) > 1 else f"FAIL (single voice: {voices})"
            )

            await _say(ws, "Can we just review old vocabulary today instead?", tmp_path, "focus")
            new_log, pos = _log_tail(pos)
            results["focus_marker"] = "PASS" if _FOCUS_RE.search(new_log) else "FAIL (no [FOCUS:] marker observed)"

    print("\n=== Tutor LLM quality gate ===")
    for name, outcome in results.items():
        print(f"  {name}: {outcome}")

    failed = [name for name, outcome in results.items() if outcome.startswith("FAIL")]
    assert not failed, f"quality gate failed: {failed} — see printed detail above (run with -s)"
