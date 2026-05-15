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

The assistant is language-agnostic: any primary language is supported as long as it is
covered by both the STT engine (faster-whisper, ~99 languages) and the TTS engine
(XTTS v2, ~17 languages). The intersection is the effective language ceiling.

---

## Goals

Build a two-component monorepo voice assistant with persistent, structured memory.

### Client

- Capture microphone audio, apply VAD, stream raw PCM to server over binary WebSocket frames
- Play back synthesized speech (received as binary WebSocket frames); suppress VAD during
  playback via mic muting (`speaking_end` signal from server)
- Stateless — no local config, no persistent state; single launch command: `memai`
- Auto-establish SSH tunnel to server on launch

### Server (GPU-equipped machine)

- Real-time pipeline: STT (faster-whisper) → LLM (ollama/llama3.3, streamed) → TTS
  (XTTS v2 / Coqui), sentence-by-sentence synthesis for low latency
- Persistent memory via PostgreSQL + pgvector (1024-dim embeddings, multilingual-e5-large)
- Three bounded contexts: **Interaction** (live session), **Memory** (consolidation, RAG),
  **Persona** (catalogue management)
- LLM context management:
  - Static MemoryBrief injected at session start
  - Session tail injection: last N turns from previous session (if within recency threshold)
    to support natural conversation continuation across sessions
  - Rolling window summarisation (configurable turn-count watermark, async between turns)
  - Explicit-recall RAG triggered by `RecallTriggered` domain event
- Off-session consolidation: group turns into logical Conversations (using boundary
  markers written during live session); extract Episodes, Concepts, Procedures; upsert
  via embedding similarity; regenerate MemoryBrief
- Session log files (JSONL per session) as primary write; DB secondary; unwritten turns
  replayed on restart — DB writes happen only offline, never during live conversation

### Onboarding

On first launch the server detects that `User.primary_language` is not set. The flow:

1. Server sends `{"type": "select_language", "supported": [...]}` immediately after connection
2. Client renders an interactive terminal selection (`questionary` dropdown) listing all
   supported languages — the user picks with arrow keys, confirms with Enter
3. Client sends `{"type": "language_selected", "language": "<lang_code>"}` back to server
4. Server sets `User.primary_language` and starts an onboarding voice conversation in
   that language

The onboarding conversation:
- Introduces the assistant and explains the concept (local, private, voice-controlled)
- Explains key capabilities: persistent memory, configurable personas, multilingual support
- Explains that everything is configured by voice going forward — no CLI arguments
- Confirms the selected language aloud

Onboarding is a regular session with a dedicated system prompt. The terminal prompt
happens only once; all subsequent sessions connect and start immediately.

### Supported Languages

Defined as a domain constant (`SUPPORTED_LANGUAGES`) — the intersection of faster-whisper
and XTTS v2 support. XTTS v2 is the limiting factor (~17 languages):

`en, fr, es, de, it, pt, pl, tr, ru, nl, cs, ar, zh-cn, ja, ko, hu, hi`

### Domain model highlights

- `User` (singleton): `primary_language: Language | None`, `secondary_languages`
- `Turn`, `SessionContext` (runtime only — rolling window state, session tail, active persona)
- `Conversation` (offline aggregate): logical grouping of Turns determined by LLM during
  consolidation; may span multiple session files or be subdivided within one session
- `Episode`, `Concept`, `Procedure` — all with 1024-dim vector embeddings
- `MemoryBrief` (singleton, written by Memory, read by Interaction at session start)
- `AssistantPersona` — `GeneralAssistant` system persona (seeded, immutable); additional
  personas created by user via co-creation voice dialogue
- `EngagementLevel`: `mentioned → explored → practiced → integrated`
- `SessionInfo` (ports value object): previous session metadata read from log files
- `PrimaryLanguageChanged` domain event: fired on User when `primary_language` is updated
- `RecallTriggered` domain event: fired on explicit recall intent; triggers RAG retrieval
- `PersonaSwitched` domain event: fired on explicit persona switch
- `ConversationBoundaryDetected` domain event: fired when LLM emits `[TOPIC_BREAK]` or
  `[TOPIC_CONTINUATION]` markers; written as boundary markers in session log files

---

## Language Model

**Primary language** — mandatory, owned by the User entity.

- Set during onboarding (first launch) via voice
- Can be changed at any time via voice; fires `PrimaryLanguageChanged`
- Drives STT language hint, LLM system prompt language, and TTS voice selection
- Any change announced aloud at session start

**Additional languages** — optional, user-managed via voice.

- Tracked as `secondary_languages` on User
- No implicit persona suggestion — only explicit persona switches supported
- Per-turn language detected by STT (returns `tuple[str, Language]`)

---

## Non-goals

- Cloud services, SaaS APIs, or any external data transmission
- Multi-user support, authentication, or row-level security
- Mobile or web client
- Barge-in / mid-stream LLM interruption
- Log rotation (all raw session logs kept forever)
- CLI arguments for language or configuration (voice-only philosophy)

---

## Constraints

| Constraint | Detail |
|---|---|
| **Hardware split** | Client: any OS (currently Windows; multi-OS support TBD); Server: any GPU-equipped machine (CUDA required; ROCm/Metal long-term goals) |
| **Local-only** | All models run offline: faster-whisper, ollama, XTTS v2, multilingual-e5-large, pgvector |
| **Language ceiling** | Intersection of faster-whisper (~99 languages) and XTTS v2 (~17 languages) |
| **Live/offline boundary** | Live conversation: flat file writes only. Offline: DB, LLM extraction, embedding, vector search |
| **Architecture** | Strict Clean Architecture — domain and services never import infrastructure |
| **Latency** | Sentence-by-sentence TTS; rolling window summarisation fires between turns (zero latency impact) |
| **Single user** | No concurrency model needed |
| **Audio transport** | Raw binary WebSocket frames in both directions — no encoding overhead |

---

## Open Questions

None.

---

## Recommended Next Step

**Phase 3 — Infrastructure adapters**, sequenced inside-out:

1. PostgreSQL repositories (users, personas, conversations, episodes, concepts, procedures, memory_brief)
2. `JSONLTurnLogger` + `JSONLSessionLogReader` (flat file session management)
3. faster-whisper STT adapter
4. XTTS v2 TTS adapter
5. Ollama LLM adapter + consolidation extractors
6. SentenceTransformer embedding service
7. WebSocket layer wiring client ↔ server
