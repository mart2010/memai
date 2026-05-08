# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI voice assistant that runs entirely on local, open-source infrastructure — no cloud services. It is a monorepo with two independent Python packages:

- **`client/`** — runs on a Windows laptop; captures microphone audio and plays back synthesized speech
- **`server/`** — runs on an Ubuntu workstation with GPU; handles STT, LLM, and TTS

## Environment Setup

Each package has its own virtual environment. Python 3.13+ required.

```bash
# Server (Ubuntu with GPU)
cd server
uv venv && uv pip install -e .

# Client (Windows)
cd client
uv venv && uv pip install -e .
```

## Running the Components

```bash
# Start server (Ubuntu)
cd server
.venv/bin/memai-server

# Start client (Windows) — SSH tunnel to server is started automatically
cd client
.venv/Scripts/memai-client
```

## Linting

Ruff is configured at the monorepo root with `line-length = 120`. Test files are excluded from linting.

```bash
ruff check .
ruff format .
```

## Architecture

### Data Flow

```
Microphone → [VAD] → WebSocket → [STT] → [LLM stream] → [TTS] → WebSocket → Speaker
  (client)                        (server)                                    (client)
```

### WebSocket Protocol

Both sides exchange newline-delimited JSON frames on `ws://localhost:8765`:

| Message type | Direction | Payload |
|---|---|---|
| `{"type": "audio", "data": [...]}` | client→server | Raw PCM int16 bytes as JSON list |
| `{"type": "end_utterance"}` | client→server | Signals end of speech segment |
| `{"type": "audio", "data": [...]}` | server→client | Synthesized float32 audio bytes as JSON list |

### Client (`client/src/memai_client/client.py`)

- Uses `sounddevice` to capture 16kHz mono audio in 30ms frames
- `webrtcvad` (aggressiveness=2) determines if a frame contains speech
- Accumulates speech frames; after 10 consecutive silent frames sends `end_utterance`
- Auto-establishes an SSH tunnel (`localhost:8765 → tx940094.open.etat-de-vaud.ch:8765`) before connecting
- Proxy environment variables are cleared to avoid interference with the SSH tunnel

### Server (`server/src/memai_server/server.py`)

- **STT**: `faster-whisper` (small model, CPU, int8) from `~/models/faster-whisper-small`, French language forced
- **LLM**: `ollama` with `llama3.3`, streamed token by token, system prompt enforces French concise replies
- **TTS**: `piper` with `~/models/piper/fr_FR-siwis-medium.onnx`; synthesis is triggered per sentence (on `.`, `!`, `?`) to minimize latency
- Audio accumulates in a `float32` numpy buffer; on `end_utterance` the buffer is transcribed and the pipeline runs

### Key Constants

| Constant | Value | Location |
|---|---|---|
| `SAMPLE_RATE` | 16000 Hz | both |
| `FRAME_DURATION` | 30 ms | client |
| WebSocket port | 8765 | both |
| LLM model | `llama3.3` | server |
| TTS voice | `fr_FR-siwis-medium` | server |
