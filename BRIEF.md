# Project Brief — Memai

## Context

Memai is a personal AI voice assistant designed to run entirely on local, open-source
infrastructure — no cloud services, no external API calls, no telemetry. The motivation
is full data ownership and privacy: conversations, memories, and learned knowledge stay
on the user's own machines.

The system is built for a single user who interacts via voice in daily life: general
conversation, language practice, knowledge exploration, and task procedures. Over time,
the assistant accumulates a structured memory of that person — episodic, semantic, and
procedural knowledge that makes it genuinely more useful with each session.

---

## Goals

Build a two-component monorepo voice assistant with persistent, structured memory.

### Client (Windows laptop)

- Capture microphone audio, apply VAD, stream raw PCM to server over binary WebSocket frames
- Play back synthesized speech (received as binary WebSocket frames); suppress VAD during
  playback via mic muting (`speaking_end` signal from server)
- Accept a `--lang` CLI argument at launch (language code, defaults to `en`); seed or
  update the User entity's `primary_language` on the server before the session starts
- Announce any primary language change aloud at session start to avoid surprises
- Auto-establish SSH tunnel to server on launch

### Server (Ubuntu workstation, GPU)

- Real-time pipeline: STT (faster-whisper) → LLM (ollama/llama3.3, streamed) → TTS
  (piper), sentence-by-sentence synthesis for low latency
- Persistent memory via PostgreSQL + pgvector (1024-dim embeddings, multilingual-e5-large)
- Three bounded contexts: **Interaction** (live session), **Memory** (consolidation, RAG),
  **Persona** (catalogue management)
- LLM context management:
  - Static MemoryBrief injected at session start
  - Rolling window summarisation (configurable turn-count watermark, async between turns)
  - Explicit-recall RAG triggered by `RecallTriggered` domain event
- Off-session consolidation: extract Episodes, Concepts, Procedures from each
  ConversationRecord; upsert via embedding similarity; regenerate MemoryBrief
- Flat-file WAL (JSONL per session) as primary write; DB secondary; replay unwritten
  turns on restart

### Domain model highlights

- `User` (singleton): `primary_language` (Language value object), list of
  `LanguageProficiency` (language, CEFRLevel, is_native)
- `LiveConversation` (runtime only), `Turn`, `ConversationRecord`
- `Episode`, `Concept`, `Procedure` — all with 1024-dim vector embeddings
- `MemoryBrief` (singleton, written by Memory, read by Interaction at session start)
- `AssistantPersona` — `GeneralAssistant` system persona (seeded, immutable); additional
  personas created by user via co-creation voice dialogue
- `EngagementLevel` value object: `mentioned → explored → practiced → integrated`
- `PrimaryLanguageChanged` domain event: fired on User when `primary_language` is updated;
  STT and TTS adapters react to reconfigure (language hint, voice selection)
- `RecallTriggered` domain event: fired on explicit recall intent; triggers RAG retrieval
- `PersonaSwitch` domain event: fired on explicit persona switch
- `PersonaSuggested` domain event: fired when a learning language is detected in a Turn

---

## Language Model

**Primary language** — mandatory, owned by the User entity.

- Seeded from `--lang` CLI argument on first launch (defaults to `en`)
- Subsequent launches with a different code update User and fire `PrimaryLanguageChanged`
- Drives STT language hint, LLM system prompt language, and TTS voice selection
- Any change announced aloud at session start

**Additional languages** — optional, user-managed via voice. Two sub-cases:

- *Learning languages*: tracked as `LanguageProficiency` on User; trigger implicit
  `PersonaSuggested` event when a Turn's detected language matches a learning language
  (only when GeneralAssistant is active)
- *Ad-hoc switches*: user speaks in another language without learning intent; detected
  and handled gracefully — no persona suggestion fired

---

## Non-goals

- Cloud services, SaaS APIs, or any external data transmission
- Multi-user support, authentication, or row-level security
- Mobile or web client
- Barge-in / mid-stream LLM interruption
- Asian language support (deferred)
- Log rotation (all raw session logs kept forever)
- Config file for primary language (CLI arg + User entity is sufficient)

---

## Constraints

| Constraint | Detail |
|---|---|
| **Hardware split** | Client: Windows laptop (no GPU); Server: Ubuntu workstation (GPU required) |
| **Local-only** | All models run offline: faster-whisper, ollama, piper, multilingual-e5-large, pgvector |
| **Language** | Primary language is a mandatory User entity field; multilingual-e5-large covers European languages (Tier 1) |
| **Architecture** | Strict Clean Architecture — domain and use cases never import infrastructure |
| **Latency** | Sentence-by-sentence TTS; rolling window summarisation fires between turns (zero latency impact) |
| **Single user** | No concurrency model needed |
| **Audio transport** | Raw binary WebSocket frames in both directions — no encoding overhead |

---

## Open Questions

None.

---

## Recommended Next Step

**Implementation planning** — produce a phased build plan sequenced inside-out:

1. Domain entities and value objects (no infrastructure, fully unit-testable)
2. Use case layer with Fake* ports (`FakeLLM`, `FakeSTT`, `FakeTTS`, `FakeRepository`,
   `FakeEmbeddingService`)
3. Infrastructure adapters one at a time (PostgreSQL, faster-whisper, ollama, piper,
   multilingual-e5-large)
4. WebSocket layer wiring client ↔ server
5. Consolidation pipeline end-to-end
6. MemoryBrief generation and session injection
