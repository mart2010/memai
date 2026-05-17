# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI voice assistant that runs entirely on local, open-source infrastructure — no cloud services. It is a monorepo with two independent Python packages:

- **`client/`** — runs on the user's machine; captures microphone audio and plays back synthesized speech. Currently developed on Windows; multi-OS support is planned but not yet implemented (approach TBD).
- **`server/`** — runs on any GPU-equipped machine; handles STT, LLM, and TTS. Currently developed on Ubuntu; other GPU-capable OS are in scope.

The assistant is **language-agnostic**: any primary language is supported as long as it is covered by both faster-whisper (~99 languages) and XTTS v2 (~17 languages). Development is not French-specific.

## Environment Setup

Each package has its own virtual environment. Python 3.13+ required.

```bash
# Server (GPU machine)
cd server
uv venv && uv pip install -e .
# Then replace CPU torch with CUDA build — CUDA (NVIDIA) is the current GPU backend; broader GPU support (ROCm, Metal) is a long-term goal

# Client
cd client
uv venv && uv pip install -e .
```

## Running the Components

```bash
# Start server (GPU machine)
cd server
.venv/bin/memai-server          # Linux/macOS
# .venv/Scripts/memai-server   # Windows

# Start client — SSH tunnel to server is started automatically
cd client
.venv/Scripts/memai-client      # Windows (current)
# .venv/bin/memai-client        # Linux/macOS (planned)
```

## Linting

Ruff is configured at the monorepo root with `line-length = 120`. Test files are excluded from linting.

```bash
ruff check .
ruff format .
```

## Design Constraints

- **Voice-only configuration** — everything is configured by voice; no CLI arguments, no
  config files for the user to edit. Any new feature or setting must be reachable through
  conversation, not through flags or environment variables.
- **Single user** — no concurrency model, no authentication, no row-level security. All
  design decisions can assume exactly one user.
- **No barge-in** — mid-stream LLM interruption is out of scope. The TTS response plays
  to completion before the mic is re-enabled.
- **Session logs are kept forever** — no log rotation. Raw JSONL session files accumulate
  indefinitely; do not introduce any cleanup or rotation logic without explicit discussion.
- **Secondary languages: explicit switches only** — `User.secondary_languages` is tracked
  but switching between them is always explicit (user asks to switch). There is no implicit
  persona suggestion when a different language is detected mid-conversation.

## Architecture

### Live / Offline Boundary

**Live conversation** — DB reads are allowed (session start: User, MemoryBrief, Persona;
RAG recall turns: Concept/Episode/Procedure similarity search). Writes go only to local JSONL session
log files. No DB writes, no embedding generation for storage, no consolidation or upsert.

**Offline (post-disconnect)** — all heavy processing: DB writes, consolidation,
LLM extraction, embedding generation for storage, pgvector upsert similarity search,
MemoryBrief generation.

This boundary is a hard invariant. Any DB write or consolidation logic that bleeds into
the live conversation path must be flagged and rejected.

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
| `{"type": "language_selected", "language": "<lang_code>"}` | client→server | Sent once during onboarding after user picks from terminal selection |
| `{"type": "select_language", "supported": [...]}` | server→client | Sent on connect when `User.primary_language` is null; client renders terminal dropdown |
| `{"type": "speaking_end"}` | server→client | Re-enables VAD on client |
| binary frame | server→client | Synthesized float32 audio bytes |

### Client (`client/src/memai_client/client.py`)

- Uses `sounddevice` to capture 16kHz mono audio in 30ms frames
- `webrtcvad` (aggressiveness=2) determines if a frame contains speech
- Accumulates speech frames; after 10 consecutive silent frames sends `end_utterance`
- Suppresses VAD from playback start until `speaking_end` received (mic muting)
- Auto-establishes an SSH tunnel (`localhost:{WS_PORT} → {SSH_USER_HOST}:{WS_PORT}`) before connecting; both values come from env vars (`SSH_USER_HOST` required, `WS_PORT` defaults to 8765)
- Stateless — no local config or persistent state of any kind
- On connect: if server sends `select_language`, renders a `questionary` terminal dropdown
  listing supported languages; user selects once; result sent as `language_selected`

### Server (`server/src/memai_server/server.py`)

- **STT**: `faster-whisper` — language auto-detected by Whisper (no forced language);
  returns `tuple[str, Language]`
- **LLM**: `ollama` with `llama3.3`, streamed token by token
- **TTS**: `XTTS v2` (Coqui) — single multilingual model, CUDA-accelerated (current), ~17 languages
- Session log files written to `logs/sessions/YYYY-MM-DD_<session_id>.jsonl`;
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

## Data Model

### Memory types

Three types of long-term memory, all stored in PostgreSQL with 1024-dim pgvector embeddings (`multilingual-e5-large`):

| Type | Purpose | Persona-scoped |
|---|---|---|
| `Episode` | What happened, anchored to its origin conversation | No — persona traceability via `conversation.persona_snapshot` |
| `Concept` | Distilled knowledge about a subject | Yes — `persona_id` FK |
| `Procedure` | How to do something — description + optional steps | Yes — `persona_id` FK |

### Concept and Procedure: persona scope

`Concept` and `Procedure` each carry a `persona_id` FK. Similarity search during
consolidation is always scoped to the active persona before applying the upsert threshold.

**Why scoping is necessary**: the same name can mean entirely different things in different
persona contexts (e.g. "big bang" with an astronomy persona vs a pop-culture persona).
Without scoping, the upsert would merge unrelated concepts. Engagement level is also
inherently persona-specific — the user may have `integrated` a concept under one persona
and only `mentioned` it under another.

**Cascade delete is intentional**: dropping a persona removes all its concepts and
procedures. A temporary persona (e.g. exam prep for a specific course) can be fully
cleaned up by deleting the persona. Do not change to `SET NULL` without explicit
discussion — the cascade is load-bearing behaviour, not an oversight.

**`GeneralAssistant`** acts as the cross-domain catch-all for concepts that do not belong
to a specialised persona.

### Concept.description invariant

`description` is a tight LLM synthesis — the best current understanding of what the user
knows about this concept within its persona context. It is not an append log: old details
are absorbed into the synthesis on each upsert. Hard cap ~300 words, chosen to stay
safely within the 512-token input limit of `multilingual-e5-large`. Both `description`
and `embedding` are always updated together on every enrichment.

### Language field (Concept and Procedure)

`language` records the language in which the concept or procedure was first introduced.
It stays fixed even if the concept resurfaces in another language. The description is
always maintained in this original language; content from other-language conversations is
translated and synthesised into the existing description during the upsert LLM call.

### Upsert similarity threshold

Consolidation uses a two-tier threshold (exact values to be calibrated on real data):

| Similarity | Action |
|---|---|
| > 0.93 | Auto-merge — almost certainly the same concept |
| 0.75 – 0.93 | Send both to LLM for disambiguation: same concept or distinct? |
| < 0.75 | Auto-insert as new concept |

The middle-band LLM call is cheap (binary judgment, runs offline) and handles cases where
embedding proximity alone is ambiguous (e.g. "golden retriever" vs "dog").

### Episode.origin_conversation_id

`origin_conversation_id` is NOT NULL — episodes are always extracted from a conversation,
never invented. It records provenance (where we first learned about this event), not
ownership. `happened_at` is the real temporal anchor: when the event occurred in the
real world, which may predate the conversation significantly.

When the same episode is revisited in later conversations, the upsert updates `summary`
and `embedding` in place; `origin_conversation_id` stays fixed. Full multi-conversation
traceability (which conversations touched a given episode) is deferred — an
`episode_conversations` join table would be the path if needed.

### Procedure.description and steps

A procedure has both `description` (NOT NULL) and `steps` (defaults to `{}`):

- `description` is a free-form LLM synthesis — same invariant as `Concept.description`
  (~300 words, always in the original language). It is the primary carrier of knowledge
  and is always populated.
- `steps` is a flat ordered array, populated only when the procedure decomposes cleanly
  into discrete sequential actions. Left empty for heuristics, principles, or any
  procedural knowledge that does not have a natural step structure.

Both `description` and `embedding` are updated together on every upsert.
