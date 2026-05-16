# Memai

**Your personal AI voice assistant — 100% private, 100% local, never leaves your home network.**

No cloud. No subscriptions. No data ever sent to a third party. Memai runs entirely on your own hardware, using open-source models, and keeps every conversation, memory, and learned insight locked inside your home network.

---

## What it does

Memai is a real-time voice assistant that listens, thinks, and speaks — and actually gets to know you over time.

- **Talk naturally.** Speak into your microphone; Memai transcribes, reasons, and responds with synthesised speech in under a second.
- **Remembers everything that matters.** After each session, Memai consolidates your conversations into structured long-term memory: episodic events, domain knowledge (Concepts), and how-to knowledge (Procedures). It recalls relevant memories when you need them.
- **Grows with you.** Engagement levels track how well you know a subject — from first mention to full integration. Memory briefs keep the assistant grounded in your personal context at every session start.
- **Multiple personas.** Switch between specialised assistants (language tutor, study partner, general assistant…) by voice, each with its own knowledge scope.
- **Speaks your language.** Supports 17 languages out of the box: `en fr es de it pt pl tr ru nl cs ar zh-cn ja ko hu hi`.

---

## Architecture

Memai is a two-component monorepo:

```
Microphone → [VAD] → WebSocket → [STT] → [LLM stream] → [TTS] → WebSocket → Speaker
  (client)                        (server)                                    (client)
```

| Component | Runs on | Role |
|---|---|---|
| `client/` | Your everyday machine (Windows today, multi-OS planned) | Captures audio, plays back speech, auto-opens SSH tunnel to server |
| `server/` | Any GPU-equipped machine on your network | STT → LLM → TTS pipeline, persistent memory, consolidation |

**All models run locally:**

| Task | Model |
|---|---|
| Speech-to-text | `faster-whisper` |
| Language model | `llama3.3` via `ollama` (streamed) |
| Text-to-speech | `XTTS v2` (Coqui), CUDA-accelerated |
| Embeddings | `multilingual-e5-large` (1024-dim) |
| Vector search | PostgreSQL + `pgvector` (HNSW index) |

---

## Getting started

**Requirements:** Python 3.13+, a GPU server with CUDA, PostgreSQL with pgvector.

```bash
# Server (GPU machine — Ubuntu)
cd server
uv venv && uv pip install -e .
# Replace CPU torch with the CUDA build for your toolkit version
memai-server

# Client (your machine — Windows)
cd client
uv venv && uv pip install -e .
# Set SSH_USER_HOST=user@your-gpu-machine in your environment
memai-client
```

On first launch, Memai guides you through language selection. After that, everything is configured by voice — no CLI arguments, no config files to edit.

---

## Privacy by design

- **Zero external calls.** No API keys, no telemetry, no model downloads after setup.
- **Your data, your disk.** Conversations are written to local JSONL files; structured memory lives in your PostgreSQL instance.
- **Air-gapped capable.** Once models are downloaded, the system runs with no internet access whatsoever.
- **Live/offline boundary.** During a conversation, only flat files are written. Heavy processing (DB writes, LLM extraction, embedding generation) happens offline, after the session ends — keeping latency low and your conversation data from touching a DB in real time.

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. See the [LICENSE](LICENSE) file for details.
