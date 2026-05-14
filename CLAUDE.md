# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI voice assistant that runs entirely on local, open-source infrastructure — no cloud services. It is a monorepo with two independent Python packages:

- **`client/`** — runs on a Windows laptop; captures microphone audio and plays back synthesized speech
- **`server/`** — runs on an Ubuntu workstation with GPU; handles STT, LLM, and TTS

The assistant is **language-agnostic**: any primary language is supported as long as it is covered by both faster-whisper (~99 languages) and XTTS v2 (~17 languages). Development is not French-specific.

## Environment Setup

Each package has its own virtual environment. Python 3.13+ required.

```bash
# Server (Ubuntu with GPU)
cd server
uv venv && uv pip install -e .
# Then replace CPU torch with CUDA build (see pyproject.toml comment on TTS dependency)

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

### Live / Offline Boundary

**Live conversation** — writes only to local JSONL session log files. No DB writes, no
embedding, no vector search during the real-time voice loop.

**Offline (post-disconnect)** — all heavy processing: DB reads/writes, consolidation,
LLM extraction, embedding generation, pgvector similarity search, MemoryBrief generation.

This boundary is a hard invariant. Any design that bleeds DB or heavy compute into the
live conversation path must be flagged and rejected.

### Data Flow

```
Microphone → [VAD] → WebSocket → [STT] → [LLM stream] → [TTS] → WebSocket → Speaker
  (client)                        (server)                                    (client)
```

### WebSocket Protocol

Audio is sent as raw binary WebSocket frames; control messages use JSON text frames on `ws://localhost:8765`:

| Message type | Direction | Payload |
|---|---|---|
| binary frame | client→server | Raw PCM int16 bytes |
| `{"type": "end_utterance"}` | client→server | Signals end of speech segment |
| `{"type": "language_selected", "language": "fr"}` | client→server | Sent once during onboarding after user picks from terminal selection |
| `{"type": "select_language", "supported": [...]}` | server→client | Sent on connect when `User.primary_language` is null; client renders terminal dropdown |
| `{"type": "speaking_end"}` | server→client | Re-enables VAD on client |
| binary frame | server→client | Synthesized float32 audio bytes |

### Client (`client/src/memai_client/client.py`)

- Uses `sounddevice` to capture 16kHz mono audio in 30ms frames
- `webrtcvad` (aggressiveness=2) determines if a frame contains speech
- Accumulates speech frames; after 10 consecutive silent frames sends `end_utterance`
- Suppresses VAD from playback start until `speaking_end` received (mic muting)
- Auto-establishes an SSH tunnel (`localhost:8765 → tx940094.open.etat-de-vaud.ch:8765`) before connecting
- Stateless — no local config or persistent state of any kind
- On connect: if server sends `select_language`, renders a `questionary` terminal dropdown
  listing supported languages; user selects once; result sent as `language_selected`

### Server (`server/src/memai_server/server.py`)

- **STT**: `faster-whisper` — language auto-detected by Whisper (no forced language);
  returns `tuple[str, Language]`
- **LLM**: `ollama` with `llama3.3`, streamed token by token
- **TTS**: `XTTS v2` (Coqui) — single multilingual model, GPU-accelerated, ~17 languages
- Session log files written to `logs/conversations/YYYY-MM-DD_<session_id>.jsonl`;
  one JSON line per turn plus inline boundary markers

### Server Package Layout

```
server/src/memai_server/
  domain/       — entities, value objects, events, protocols (no external imports)
  services/     — use cases / application logic; defines abstract ports
  infrastructure/  — concrete adapters (Phase 3+)
```

### Key Constants

| Constant | Value | Location |
|---|---|---|
| `SAMPLE_RATE` | 16000 Hz | both |
| `FRAME_DURATION` | 30 ms | client |
| WebSocket port | 8765 | both |
| LLM model | `llama3.3` | server |
