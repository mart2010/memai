import asyncio
import json
import os
from pathlib import Path

import numpy as np
import ollama
import websockets
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from piper import PiperVoice

load_dotenv()

SAMPLE_RATE = 16000
WS_PORT = int(os.getenv("WS_PORT", "8765"))
LANGUAGE = os.getenv("LANGUAGE", "fr")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.3")
WHISPER_MODEL_PATH = Path(os.getenv("WHISPER_MODEL_PATH", "~/models/faster-whisper-small")).expanduser()
PIPER_MODEL_PATH = Path(os.getenv("PIPER_MODEL_PATH", "~/models/piper")).expanduser()
PIPER_VOICE = os.getenv("PIPER_VOICE", "fr_FR-siwis-medium")

whisper = WhisperModel(str(WHISPER_MODEL_PATH), device="cpu", compute_type="int8")

voice_fr = PiperVoice.load(
    str(PIPER_MODEL_PATH / f"{PIPER_VOICE}.onnx"),
    config_path=str(PIPER_MODEL_PATH / f"{PIPER_VOICE}.onnx.json"),
)

SYSTEM_PROMPT_FR = (
    "Tu es un assistant vocal francophone. "
    "Réponds toujours en français, de façon concise et naturelle."
)


# -------- STT --------
def transcribe(audio):
    segments, _ = whisper.transcribe(audio, beam_size=1, language=LANGUAGE)
    return " ".join([s.text for s in segments]).strip()

# -------- LLM STREAM --------
async def stream_llm(text):
    loop = asyncio.get_event_loop()

    def run_ollama():
        return ollama.chat(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_FR},
                {"role": "user", "content": text},
            ],
            stream=True,
        )

    stream = await loop.run_in_executor(None, run_ollama)

    for chunk in stream:
        if "message" in chunk and "content" in chunk["message"]:
            yield chunk["message"]["content"]


# -------- Piper TTS --------
def synthesize(text):
    raw = b"".join(chunk.audio_int16_bytes for chunk in voice_fr.synthesize(text))
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio.tobytes()


# -------- Sentence buffer --------
def is_sentence_end(buffer):
    return any(buffer.endswith(p) for p in [".", "!", "?"])

# -------- Handler --------
async def handler(ws):
    print("Client connected")

    audio_buffer = np.array([], dtype=np.float32)

    async for msg in ws:
        data = json.loads(msg)

        if data["type"] == "audio":
            chunk = np.frombuffer(bytes(data["data"]), dtype=np.int16).astype(np.float32) / 32768.0
            audio_buffer = np.concatenate([audio_buffer, chunk])

        elif data["type"] == "end_utterance":
            if len(audio_buffer) == 0:
                continue

            text = transcribe(audio_buffer)
            print("User:", text)

            audio_buffer = np.array([], dtype=np.float32)

            buffer = ""
            async for token in stream_llm(text):
                buffer += token
                print("Token:", token)
                if is_sentence_end(buffer):
                    audio_bytes = synthesize(buffer)
                    await ws.send(json.dumps({
                        "type": "audio",
                        "data": list(audio_bytes)
                    }))
                    buffer = ""

    print("Client disconnected")


async def _run():
    async with websockets.serve(handler, "0.0.0.0", WS_PORT):
        print("Server ready")
        await asyncio.Future()


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
