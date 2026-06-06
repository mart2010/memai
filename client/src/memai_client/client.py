# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import asyncio
import json
import os
import subprocess
import threading
import time

import numpy as np
import questionary
import sounddevice as sd
import websockets
import webrtcvad
from dotenv import load_dotenv

load_dotenv()

SAMPLE_RATE = 16000
FRAME_DURATION = 30  # ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION / 1000)
WS_PORT = int(os.getenv("WS_PORT", "8765"))
SSH_USER_HOST = os.environ["SSH_USER_HOST"]

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


def _start_ssh_tunnel() -> None:
    cmd = ["ssh", "-N", "-L", f"{WS_PORT}:localhost:{WS_PORT}", SSH_USER_HOST]

    def _run() -> None:
        while True:
            print("Starting SSH tunnel...")
            proc = subprocess.Popen(cmd)
            proc.wait()
            print("SSH tunnel stopped. Restarting in 3s...")
            time.sleep(3)

    threading.Thread(target=_run, daemon=True).start()


async def _select_language(supported: list[str]) -> str:
    loop = asyncio.get_running_loop()
    choice = await loop.run_in_executor(
        None,
        lambda: questionary.select("Select your language:", choices=supported).ask(),
    )
    return choice or supported[0]


async def run() -> None:
    _start_ssh_tunnel()

    loop = asyncio.get_running_loop()

    async with websockets.connect(f"ws://localhost:{WS_PORT}") as ws:
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
                if silence_counter > 10:
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
                    # Mute mic for the full duration of playback
                    _mic_active.clear()
                    sd.play(np.frombuffer(msg, dtype=np.float32), SAMPLE_RATE)
                    await loop.run_in_executor(None, sd.wait)
                elif isinstance(msg, str):
                    data = json.loads(msg)
                    msg_type = data.get("type")
                    if msg_type == "select_language":
                        # Mute mic during terminal interaction so VAD prints don't interfere
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
