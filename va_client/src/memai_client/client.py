import asyncio
import os
import subprocess
import threading
import time

import numpy as np
import sounddevice as sd
import websockets
import webrtcvad
import json
from dotenv import load_dotenv

load_dotenv()

SAMPLE_RATE = 16000
FRAME_DURATION = 30  # ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION / 1000)
WS_PORT = int(os.getenv("WS_PORT", "8765"))
SSH_USER_HOST = os.environ["SSH_USER_HOST"]

# to disable proxy settings that might interfere with SSH tunnel
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)

SSH_CMD = [
    "ssh",
    "-N",
    "-L", f"{WS_PORT}:localhost:{WS_PORT}",
    SSH_USER_HOST,
]

# -------- SSH tunnel --------
def start_ssh_tunnel():
    def run_ssh():
        while True:
            print("Starting SSH tunnel...")
            process = subprocess.Popen(SSH_CMD)
            process.wait()
            print("SSH tunnel stopped. Restarting in 3s...")
            time.sleep(3)

    thread = threading.Thread(target=run_ssh, daemon=True)
    thread.start()

# -------- VAD --------
vad = webrtcvad.Vad(2)

def is_speech(frame):
    return vad.is_speech(frame, SAMPLE_RATE)

# -------- Audio stream --------
async def run():
    start_ssh_tunnel()

    loop = asyncio.get_running_loop()
    uri = f"ws://localhost:{WS_PORT}"

    async with websockets.connect(uri) as ws:
        print("Connected")

        silence_counter = 0
        speech_active = False

        def callback(indata, frames, time, status):
            nonlocal silence_counter, speech_active

            pcm16 = (indata.flatten() * 32768).astype(np.int16)
            frame = pcm16.tobytes()

            if is_speech(frame):
                if not speech_active:
                    print("Speech detected")
                speech_active = True
                silence_counter = 0

                asyncio.run_coroutine_threadsafe(
                    ws.send(json.dumps({
                        "type": "audio",
                        "data": list(frame)
                    })),
                    loop
                )
            else:
                if speech_active:
                    silence_counter += 1

                if silence_counter > 10:
                    print("End of utterance, sending to server...")
                    speech_active = False
                    silence_counter = 0

                    asyncio.run_coroutine_threadsafe(
                        ws.send(json.dumps({"type": "end_utterance"})),
                        loop
                    )

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            blocksize=FRAME_SIZE,
            dtype="float32",
            callback=callback,
        )

        stream.start()

        # playback loop
        while True:
            msg = await ws.recv()
            data = json.loads(msg)

            if data["type"] == "audio":
                audio = np.frombuffer(bytes(data["data"]), dtype=np.float32)
                sd.play(audio, SAMPLE_RATE)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
