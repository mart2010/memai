# PLAN.md — Memai Implementation Plan

## Status Legend
- `[ ]` not started
- `[~]` in progress
- `[x]` done

## Starting Point

A working real-time voice pipeline exists in `server/server.py` and `client/client.py`:
STT (faster-whisper) → LLM (ollama, streamed) → TTS (piper), connected over binary
WebSocket frames. The domain layer, service layer, memory system, and test infrastructure
are built (Phases 1–2). Existing pipeline logic will be extracted into proper adapters
during Phase 3.

---

## Phase 1 — Domain Layer

Pure Python. No imports from outer layers. Fully unit-testable in isolation.

### Value Objects & Enums
- [x] `Language` value object (IETF language code)
- [x] `SUPPORTED_LANGUAGES` constant — intersection of faster-whisper and Kokoro (~8 languages; Kokoro is the limiting factor)
- [x] `Speaker` enum (user, assistant)
- [x] `EngagementLevel` enum — states: mentioned | explored | practiced | integrated
- [x] `MemoryType` enum (EPISODE, CONCEPT, PROCEDURE)
- [x] `ConversationBoundaryType` enum (BREAK, CONTINUATION)

### Entities & Aggregates
- [x] `User` entity (id, primary_language: Language | None, secondary_languages)
- [x] `Turn` entity (timestamp, speaker, content, language)
- [x] `Conversation` aggregate root (id, started_at, ended_at, worthiness,
      persona_snapshot, turns, consolidated flag)
      — logical grouping determined by LLM; may span sessions or be sub-divided within one
      — invariants: ≥1 Turn + ended to consolidate; immutable once consolidated
- [x] `Episode` entity (id, summary, happened_at, conversation_id, embedding)
- [x] `Concept` entity (id, name, description, language, engagement_level, embedding)
- [x] `Procedure` entity (id, name, steps, language, engagement_level, embedding)
- [x] `MemoryBrief` singleton entity (content, created_at, updated_at)
- [x] `AssistantPersona` entity (id, name, system_prompt, languages, response_language, tts_voice, is_system, created_at, updated_at)

### Domain Events
- [x] `PrimaryLanguageChanged` (user_id, old_language, new_language)
- [x] `RecallTriggered` (query: str, memory_types: tuple[MemoryType, ...])
- [x] `PersonaSwitched` (from_persona_id, to_persona_id)
- [x] `ConversationBoundaryDetected` (boundary_type: ConversationBoundaryType)

### Domain-owned Protocols
- [x] `WorthinessEvaluator` Protocol (evaluate(conversation: Conversation) → bool)
- [x] `RecallIntentDetector` Protocol (detect(text: str) → RecallTriggered | None)
- [x] `PersonaIntentDetector` Protocol (detect(text: str) → str | None)

### Unit Tests — Phase 1
- [x] `Conversation` invariants (add_turn/consolidation guards, eligibility)
- [x] `AssistantPersona` guard (is_system cannot be modified)

---

## Phase 2 — Service Layer

Application logic. All infrastructure behind Protocols. Fake* for tests.

### Infrastructure Ports (defined here, implemented in Phase 3)
- [x] `STTService` Protocol (transcribe(audio: bytes, language_hint) → tuple[str, Language])
- [x] `LLMService` Protocol (complete(messages, system_prompt) → AsyncIterator[str])
- [x] `TTSService` Protocol (synthesise(text: str, voice: str) → bytes)
- [x] `EmbeddingService` Protocol (embed(text: str) → list[float])
- [x] `UserRepository` Protocol
- [x] `SessionLogReader` Protocol (get_previous() → SessionInfo | None; read_tail(session_id, max_turns) → list[Turn])
- [x] `SessionInfo` value object (session_id, ended_at, clean_exit: bool)
- [x] `ConversationRepository` Protocol
- [x] `MemoryRepository` Protocol (upsert + similarity_search per MemoryType)
- [x] `PersonaRepository` Protocol
- [x] `MemoryBriefRepository` Protocol
- [x] `TurnLogger` Protocol (append(session_id, turn, marker: ConversationBoundaryType | None), close(session_id, ended_at, clean_exit))

### Fake Implementations (in tests/fakes/)
- [x] `FakeSTTService`
- [x] `FakeLLMService`
- [x] `FakeTTSService`
- [x] `FakeEmbeddingService`
- [x] `FakeUserRepository`
- [x] `FakeSessionLogReader`
- [x] `FakeConversationRepository`
- [x] `FakeMemoryRepository`
- [x] `FakePersonaRepository`
- [x] `FakeMemoryBriefRepository`
- [x] `FakeTurnLogger` (tracks written turns, closed sessions, markers)

### Services — Interaction Context
- [x] `StartSession` — load User + MemoryBrief + active persona; check previous session
      recency via SessionLogReader; inject session tail if within threshold
- [x] `ProcessTurn` — detect recall intent, detect persona intent, detect conversation
      boundary markers ([TOPIC_BREAK] / [TOPIC_CONTINUATION] on first turn only), run
      STT→LLM→TTS pipeline, trigger rolling window summarisation when watermark reached,
      log turns + markers via TurnLogger; Conversation grouping is an offline concern
- [x] `EndSession` — write session_closed marker (clean_exit=True) via TurnLogger

### Services — Persona Context
- [x] `CreatePersona` (guard: only when GeneralAssistant active)
- [x] `ListPersonas`
- [x] `EditPersona` (guard: not is_system)
- [x] `RemovePersona` (guard: not is_system)
- [x] `SwitchPersona` — fire PersonaSwitched; result announced aloud

### Services — Memory Context
- [x] `TriggerRecall` — embed query → similarity search filtered by memory_types →
      inject top-N results into current turn's LLM context
- [x] `RunConsolidation` — process all unconsolidated Conversations oldest-first;
      per conversation: extract Episodes/Concepts/Procedures via upsert pattern; commit
      all writes + consolidated flag in one DB transaction
- [x] `GenerateMemoryBrief` — LLM condenses current memory state → overwrite MemoryBrief

### Services — User Management
- [x] `UpdatePrimaryLanguage` — update User.primary_language, fire PrimaryLanguageChanged

### Unit Tests — Phase 2
- [x] `StartSession` — correct MemoryBrief injection, correct persona loaded,
      tail injected within threshold / not injected beyond threshold
- [x] `ProcessTurn` — recall path (RecallTriggered fired + context injected),
      topic break / continuation markers, rolling window trigger
- [x] `EndSession` — TurnLogger closed with clean_exit=True
- [x] `RunConsolidation` — worthy vs. unworthy conversation, concepts always extracted,
      consolidated flag set, already-consolidated conversations skipped on rerun
- [x] `UpdatePrimaryLanguage` — event fired, no-op on same language
- [x] `CreatePersona` / `EditPersona` / `RemovePersona` / `SwitchPersona` — guards, event, session update

---

## Phase 3 — Infrastructure Adapters

One adapter at a time. Inner layers unchanged.

### Flat File (session logs — live path, no DB)
- [x] `JSONLTurnLogger` (`infrastructure/json_file.py`) — appends to `logs/sessions/YYYY-MM-DD_<session_id>.jsonl`;
      turn line: `{"ts": "…", "speaker": "…", "content": "…"}`;
      marker line: `{"type": "conversation_boundary"|"topic_continuation"|"session_closed", …}`
- [x] `JSONLSessionLogReader` (`infrastructure/json_file.py`) — scans log directory for most recent session file;
      reads `session_closed` marker for ended_at + clean_exit; reads tail turns

### Persistence (PostgreSQL + pgvector)
- [x] DB schema (`migrations/001_initial_schema.sql`): users, personas, conversations, turns,
      episodes, concepts, procedures, memory_brief — pgvector extension, HNSW indexes on
      embedding columns, partial index on unconsolidated conversations, GeneralAssistant seed
      — integer PKs (BIGSERIAL/SERIAL) for conversations/episodes/concepts/procedures; UUID for personas/users
      — concepts/procedures carry persona_id FK (ON DELETE CASCADE); episodes use origin_conversation_id
      — turns carry `session_id UUID NOT NULL` (source JSONL file); indexed for TurnLogReplayer idempotency
- [x] `TurnLogReplayer` — replays unprocessed JSONL session files into the DB (creates
      Conversation + Turn records); triggered two ways:
      (1) **Primary** — idle timer after clean session close: if no new session opens within
          N minutes of the `session_closed` marker, fire TurnLogReplayer → RunConsolidation
          → GenerateMemoryBrief; timer is cancelled if a new session starts first.
      (2) **Recovery** — on server start: catch any sessions not yet in the DB due to a
          crash or power loss (no `session_closed` marker present).
      Scanning strategy: walk log files **newest-first**; collect unprocessed session_ids
      (`SELECT 1 FROM turns WHERE session_id = $1 LIMIT 1`); stop immediately when a file
      whose session_id is already in the DB is encountered — all older files are guaranteed
      persisted (invariant: the replayer always commits oldest-first, so persistence is
      monotonic). Reverse the collected list and process **oldest-first** to maintain
      correct temporal ordering for conversation grouping and consolidation.
      Conversation grouping: reads `[TOPIC_BREAK]`/`[TOPIC_CONTINUATION]` markers already
      written during the live session — no new LLM inference at replay time.
- [x] `PSUserRepository` (`infrastructure/postgres.py`)
- [x] `PSConversationRepository` (`infrastructure/postgres.py`)
- [x] `PSMemoryRepository` (`infrastructure/postgres.py`) — pgvector similarity search, persona-scoped for concepts/procedures
- [x] `PSPersonaRepository` (`infrastructure/postgres.py`)
- [x] `PSMemoryBriefRepository` (`infrastructure/postgres.py`)

### STT
- [x] `FasterWhisperSTTService` (`infrastructure/stt.py`) — auto-detects language (no forced language);
      language_hint accepted but unused; CUDA float16

### LLM
- [x] `OllamaLLMService` (`infrastructure/llm.py`) — async streaming via `ollama.AsyncClient`
- [x] `OllamaWorthinessEvaluator` (`infrastructure/llm.py`) — sync one-shot, YES/NO prompt
- [x] `OllamaRecallIntentDetector` (`infrastructure/llm.py`) — sync, JSON format mode
- [x] `OllamaConsolidationExtractor` (`infrastructure/llm.py`) — sync, JSON format mode;
      extracts Episodes/Concepts/Procedures; persona_id from conversation snapshot
      — `OllamaPersonaIntentDetector` removed: persona switching is LLM self-report only
        (`_strip_persona_prefix` inline in `ProcessTurn`); no domain protocol needed

### TTS
- [x] `KokoroTTSService` (`infrastructure/tts.py`) — Kokoro (Apache-2.0); lazily initialises
      one `KPipeline` per language prefix (cached); resamples 24 kHz → 16 kHz via `resample_poly`;
      voice selected per-persona via `AssistantPersona.tts_voice`

### Embeddings
- [x] `SentenceTransformerEmbeddingService` (`infrastructure/embedding.py`) —
      `intfloat/multilingual-e5-large`, 1024-dim, L2-normalised

### Similarity threshold & merge logic
- [ ] Replace hardcoded `similarity_threshold=0.85` in `RunConsolidation` with a global
      config value — this is a critical tuning parameter
- [ ] Revisit `_cosine_similarity` / `_should_merge` in services layer: once
      `PostgresMemoryRepository.search()` returns pgvector similarity scores alongside
      results, the manual cosine check becomes redundant — simplify accordingly

### Integration Tests — Phase 3
- [ ] PostgreSQL repositories (real DB, test schema)
- [ ] `FasterWhisperSTTService` (real model, short audio fixture)
- [ ] `KokoroTTSService` (real model, short text fixture; verify default voice names match installed version)
- [ ] `SentenceTransformerEmbeddingService` — real model, calibration test: embed pairs
      of semantically similar vs. dissimilar texts and print similarity scores to help
      determine a good threshold value for the merge decision

---

## Phase 4 — WebSocket Layer (two-pass wiring)

Fully replace the PoC `server.py` with Clean Architecture wiring.
Must run on the GPU server. Do not keep PoC code alongside the real wiring — replace in full.

### Pass 1 — Thin wiring (audio loop validation)

Goal: validate the full audio loop (mic → WebSocket → STT → LLM → TTS → speaker) on real
hardware before wiring the DB. Use real services; stub the DB with in-memory repos.

- [ ] Wire `StartSession`, `ProcessTurn`, `EndSession` into a real WebSocket handler
- [ ] Real services: `FasterWhisperSTTService` (CUDA float16), `OllamaLLMService`, `KokoroTTSService`
- [ ] In-memory stubs: fixed `User` (primary_language = "en"), fixed `GeneralAssistant` persona
      (response_language = "en", tts_voice = "af_heart"), no-op `TurnLogger`
- [ ] Verify GPU is active for STT (nvidia-smi during transcription)
- [ ] Benchmark STT latency: time from `end_utterance` to first LLM token; target < ~1s
- [ ] Benchmark TTS latency: time from first complete LLM sentence to first audio chunk
- [ ] End-to-end: speak → first audio chunk back; identify dominant bottleneck
- [ ] Confirm smooth playback, no audio glitches or buffer underruns
- [ ] Verify Kokoro voice names match the installed version (see `KOKORO_DEFAULT_VOICES` in `infrastructure/tts.py`)

Only proceed to Pass 2 once the audio loop is confirmed responsive on GPU.

### Pass 2 — Full wiring

Swap in real repositories and wire the offline consolidation pipeline.

#### Server Entrypoint
- [ ] On connect: run `TurnLogReplayer` if unwritten entries exist; check `User.primary_language`
- [ ] If `primary_language` is None: send `select_language` with `SUPPORTED_LANGUAGES` list;
      await `language_selected` frame; call `UpdatePrimaryLanguage`; then start onboarding session
- [ ] Normal session: call `StartSession` (injects MemoryBrief + session tail if applicable)
- [ ] Binary frames (audio) → buffer; `end_utterance` → `ProcessTurn`
- [ ] Stream synthesised audio as binary frames; send `speaking_end` JSON frame after
      final chunk of each response
- [ ] On disconnect: call `EndSession`; start idle timer — if no new session opens within
      N minutes, fire `TurnLogReplayer` → `RunConsolidation` → `GenerateMemoryBrief` (all async,
      non-blocking); cancel timer on new connection

#### Real repositories
- [ ] `PSUserRepository`, `PSPersonaRepository`, `PSConversationRepository`,
      `PSMemoryRepository`, `PSMemoryBriefRepository`
- [ ] `JSONLTurnLogger` (live path)
- [ ] `TurnLogReplayer` (crash recovery on startup + idle timer trigger post-disconnect)
- [ ] `RunConsolidation` + `GenerateMemoryBrief` (triggered by idle timer)
- [ ] DB pre-requisite: run `001_initial_schema.sql`; insert User record before first connect

#### Client Entrypoint (refactor client.py)
- [ ] On connect: if server sends `select_language`, render `questionary` terminal dropdown
      with the supported language list; send `language_selected` result
- [ ] Suppress VAD from playback start until `speaking_end` received (mic muting)
- [ ] Existing: sounddevice capture, webrtcvad, binary frames, SSH tunnel — keep as-is

#### ⚠ Revisit: Client-side first-launch onboarding flow
Current design: server detects missing `primary_language` → pushes `select_language` to
client → client renders questionary dropdown.

Proposed change: move first-launch setup entirely to the client, using questionary for all
three prompts in sequence before attempting any connection:
1. Server address (`SSH_USER_HOST`) — saved locally (e.g. `.env`)
2. SSH/WebSocket port (`WS_PORT`) — saved locally, defaults to 8765
3. Primary language — sent to server as `language_selected` after connecting

Rationale: the client already needs server address and port before it can connect at all;
doing all three in a single client-side first-launch wizard is cleaner than a two-phase
flow (local config + server-driven prompt). Language ownership stays server-side as agreed.

Implications to resolve before implementing:
- Server should still handle the `language_selected` message and call `UpdatePrimaryLanguage`
  (no change to server protocol)
- Server no longer sends `select_language`; remove that message type from the protocol, or
  keep it as a fallback for headless/non-interactive clients
- Decide on local config format: `.env` file written by the wizard vs. a dedicated
  `config.json` — `.env` is simplest given `python-dotenv` is already a dependency
- Define "first launch" on client: absence of `SSH_USER_HOST` in `.env` (or config file)

### End-to-End Smoke Test
- [ ] Client connects, speaks a sentence, receives synthesised audio response
- [ ] First launch triggers language selection prompt; onboarding conversation starts in
      selected language

---

## Phase 5 — Consolidation Pipeline

Off-session memory consolidation runs reliably after every disconnect.

- [ ] Full offline pipeline wired: TurnLogReplayer → RunConsolidation → GenerateMemoryBrief,
      triggered by idle timer after clean disconnect (see Phase 4b)
- [ ] Oldest-first processing of all unconsolidated Conversations
- [ ] Per-conversation atomicity: Episodes + Concepts + Procedures + consolidated flag
      in one DB transaction
- [ ] Crash recovery: unconsolidated Conversations reprocessed safely on next run
- [ ] Reconnect during active consolidation: new session starts immediately with last
      committed MemoryBrief (stale is acceptable)
- [ ] End-to-end test: disconnect → verify Conversations consolidated + DB state correct

---

## Phase 6 — MemoryBrief Generation and Session Injection

The assistant has meaningful context from past conversations at every session start.

- [ ] GenerateMemoryBrief service wired at end of each full consolidation run
- [ ] MemoryBrief overwritten (single record, always current)
- [ ] StartSession injects MemoryBrief content as static system-level block
- [ ] End-to-end test: two sessions; second session's LLM context contains summary of first
