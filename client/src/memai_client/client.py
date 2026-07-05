# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import asyncio
import json
import os
import subprocess
import threading
import time
import tomllib
from pathlib import Path

import numpy as np
import questionary
import sounddevice as sd
import websockets
import webrtcvad
from platformdirs import user_config_dir

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(user_config_dir("memai", appauthor=False)) / "memai.toml"


def _load_config() -> tuple[int, str | None]:
    """Returns (ws_port, ssh_host). ssh_host is None for single-host deployments."""
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Client config not found at {_CONFIG_PATH}. "
            "Copy client/config/memai.example.toml to that location and fill in your values, "
            "or run memai-setup to generate it automatically."
        )
    with open(_CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)
    server = raw.get("server", {})
    return int(server.get("ws_port", 8765)), server.get("ssh_host") or None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000
FRAME_DURATION = 30  # ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION / 1000)

# Avoid proxy settings interfering with the SSH tunnel
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)

_vad = webrtcvad.Vad(2)

# Cleared while the assistant is speaking — suppresses VAD and print noise.
# Set at startup and re-set on every speaking_end message.
_mic_active = threading.Event()
_mic_active.set()


# ---------------------------------------------------------------------------
# SSH tunnel (split-host only)
# ---------------------------------------------------------------------------


def _start_ssh_tunnel(ws_port: int, ssh_host: str) -> None:
    cmd = ["ssh", "-N", "-L", f"{ws_port}:localhost:{ws_port}", ssh_host]

    def _run() -> None:
        while True:
            print("Starting SSH tunnel...")
            proc = subprocess.Popen(cmd)
            proc.wait()
            print("SSH tunnel stopped. Restarting in 3s...")
            time.sleep(3)

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


async def _select_language(supported: list[str]) -> str:
    loop = asyncio.get_running_loop()
    choice = await loop.run_in_executor(
        None,
        lambda: questionary.select("Select your language:", choices=supported).ask(),
    )
    return choice or supported[0]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def run() -> None:
    ws_port, ssh_host = _load_config()

    if ssh_host:
        _start_ssh_tunnel(ws_port, ssh_host)
        time.sleep(1)  # give the tunnel a moment to establish

    loop = asyncio.get_running_loop()

    async with websockets.connect(f"ws://localhost:{ws_port}", max_size=None) as ws:
        print("Connected")

        silence_counter = 0
        speech_active = False

        def callback(indata, frames, time_info, status):
            nonlocal silence_counter, speech_active

            if not _mic_active.is_set():
                return

            pcm16 = (indata.flatten() * 32768).astype(np.int16)
            frame = pcm16.tobytes()

            if _vad.is_speech(frame, SAMPLE_RATE):
                if not speech_active:
                    print("Speech detected")
                speech_active = True
                silence_counter = 0
                asyncio.run_coroutine_threadsafe(ws.send(frame), loop)
            else:
                if speech_active:
                    silence_counter += 1
                if silence_counter > 25:
                    print("End of utterance")
                    speech_active = False
                    silence_counter = 0
                    asyncio.run_coroutine_threadsafe(ws.send(json.dumps({"type": "end_utterance"})), loop)

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            blocksize=FRAME_SIZE,
            dtype="float32",
            callback=callback,
        ):
            async for msg in ws:
                if isinstance(msg, bytes):
                    _mic_active.clear()
                    sd.play(np.frombuffer(msg, dtype=np.float32), SAMPLE_RATE)
                    await loop.run_in_executor(None, sd.wait)
                elif isinstance(msg, str):
                    data = json.loads(msg)
                    msg_type = data.get("type")
                    if msg_type == "select_language":
                        _mic_active.clear()
                        lang = await _select_language(data["supported"])
                        await ws.send(json.dumps({"type": "language_selected", "language": lang}))
                        print(f"Language set: {lang}")
                        _mic_active.set()
                    elif msg_type == "speaking_end":
                        _mic_active.set()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
