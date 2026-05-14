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
- [x] `SUPPORTED_LANGUAGES` constant — intersection of faster-whisper and XTTS v2 (~17 languages)
- [x] `Speaker` enum (user, assistant)
- [x] `EngagementLevel` enum — states: mentioned | explored | practiced | integrated
- [x] `MemoryType` enum (EPISODE, CONCEPT, PROCEDURE)
- [x] `BoundaryType` enum (BREAK, CONTINUATION)

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
- [x] `MemoryBrief` singleton entity (content, generated_at)
- [x] `AssistantPersona` entity (id, name, system_prompt, languages, is_system, created_at, updated_at)

### Domain Events
- [x] `PrimaryLanguageChanged` (user_id, old_language, new_language)
- [x] `RecallTriggered` (query: str, memory_types: tuple[MemoryType, ...])
- [x] `PersonaSwitched` (from_persona_id, to_persona_id)
- [x] `ConversationBoundaryDetected` (boundary_type: BoundaryType)

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
- [x] `TTSService` Protocol (synthesise(text: str) → bytes)
- [x] `EmbeddingService` Protocol (embed(text: str) → list[float])
- [x] `UserRepository` Protocol
- [x] `SessionLogReader` Protocol (get_previous() → SessionInfo | None; read_tail(session_id, max_turns) → list[Turn])
- [x] `SessionInfo` value object (session_id, ended_at, clean_exit: bool)
- [x] `ConversationRepository` Protocol
- [x] `MemoryRepository` Protocol (upsert + similarity_search per MemoryType)
- [x] `PersonaRepository` Protocol
- [x] `MemoryBriefRepository` Protocol
- [x] `TurnLogger` Protocol (append, close(session_id, ended_at, clean_exit), write_marker)

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

---

## Phase 3 — Infrastructure Adapters

One adapter at a time. Inner layers unchanged.

### Flat File (session logs — live path, no DB)
- [ ] `JSONLTurnLogger` — appends to `logs/conversations/YYYY-MM-DD_<session_id>.jsonl`;
      turn line: `{"ts": "…", "speaker": "…", "content": "…", "db_written": false}`;
      marker line: `{"type": "conversation_boundary"|"topic_continuation"|"session_closed", …}`
- [ ] `JSONLSessionLogReader` — scans log directory for most recent session file;
      reads `session_closed` marker for ended_at + clean_exit; reads tail turns

### Persistence (PostgreSQL + pgvector)
- [ ] DB schema: users, personas, conversations, turns, episodes, concepts,
      procedures, memory_brief — with pgvector extension and HNSW index on embedding columns
- [ ] `TurnLogReplayer` — on server start: scan flat files for `db_written: false` entries,
      replay into DB before accepting connections
- [ ] `PostgresUserRepository`
- [ ] `PostgresConversationRepository`
- [ ] `PostgresMemoryRepository` (pgvector similarity_search)
- [ ] `PostgresPersonaRepository` — seed GeneralAssistant at DB init
- [ ] `PostgresMemoryBriefRepository`

### STT
- [ ] `FasterWhisperSTTService` — auto-detects language (no forced language);
      accepts primary_language as a hint; returns tuple[str, Language]

### LLM
- [ ] `OllamaLLMService` — streamed; system prompt language follows primary_language
- [ ] `OllamaWorthinessEvaluator`
- [ ] `OllamaRecallIntentDetector`
- [ ] `OllamaPersonaIntentDetector`
- [ ] `OllamaConsolidationExtractor` (extracts Episodes/Concepts/Procedures from turns)

### TTS
- [ ] `XttsTTSService` (XTTS v2 / Coqui) — single multilingual model; voice/language
      driven by primary_language; reconfigures on PrimaryLanguageChanged

### Embeddings
- [ ] `SentenceTransformerEmbeddingService` (multilingual-e5-large; runs as a subprocess
      or lightweight separate process on the Ubuntu server)

### Similarity threshold & merge logic
- [ ] Replace hardcoded `similarity_threshold=0.85` in `RunConsolidation` with a global
      config value — this is a critical tuning parameter
- [ ] Revisit `_cosine_similarity` / `_should_merge` in services layer: once
      `PostgresMemoryRepository.search()` returns pgvector similarity scores alongside
      results, the manual cosine check becomes redundant — simplify accordingly

### Integration Tests — Phase 3
- [ ] PostgreSQL repositories (real DB, test schema)
- [ ] `FasterWhisperSTTService` (real model, short audio fixture)
- [ ] `XttsTTSService` (real model, short text fixture)
- [ ] `SentenceTransformerEmbeddingService` — real model, calibration test: embed pairs
      of semantically similar vs. dissimilar texts and print similarity scores to help
      determine a good threshold value for the merge decision

---

## Phase 4 — Pipeline Tuning + WebSocket Layer

### 4a — Pipeline Tuning (before refactor)

Establish a smooth, GPU-accelerated baseline on the existing monolithic server.py before
any architectural changes. Do not refactor yet — validate performance first.

- [ ] Switch faster-whisper to CUDA device (float16 or int8 on GPU); confirm GPU is used
      via nvidia-smi during transcription
- [ ] Benchmark STT latency: measure time from end_utterance to first LLM token; target
      is perceptibly snappy (< ~1s for typical short utterance)
- [ ] Switch TTS to XTTS v2; confirm GPU acceleration; benchmark first audio chunk latency
- [ ] End-to-end latency check: speak → first audio chunk back; identify dominant
      bottleneck (STT / first LLM token / first TTS sentence)
- [ ] Confirm smooth playback with no audio glitches or buffer underruns

Only proceed to 4b once the pipeline feels responsive and GPU utilisation is confirmed.

### 4b — WebSocket Layer (refactor)

Wire client ↔ server into Clean Architecture. All domain logic already tested.

### Server Entrypoint (refactor server.py)
- [ ] On connect: run TurnLogReplayer if unwritten entries exist; check User.primary_language
- [ ] If primary_language is None: send `select_language` with SUPPORTED_LANGUAGES list;
      await `language_selected` frame; call UpdatePrimaryLanguage; then start onboarding session
- [ ] Normal session: call StartSession (injects MemoryBrief + session tail if applicable)
- [ ] Binary frames (audio) → buffer; `end_utterance` → ProcessTurn
- [ ] Stream synthesised audio as binary frames; send `speaking_end` JSON frame after
      final chunk of each response
- [ ] On disconnect: call EndSession → trigger RunConsolidation (async, non-blocking)

### Client Entrypoint (refactor client.py)
- [ ] On connect: if server sends `select_language`, render `questionary` terminal dropdown
      with the supported language list; send `language_selected` result
- [ ] Suppress VAD from playback start until `speaking_end` received (mic muting)
- [ ] Existing: sounddevice capture, webrtcvad, binary frames, SSH tunnel — keep as-is

### End-to-End Smoke Test
- [ ] Client connects, speaks a sentence, receives synthesised audio response
- [ ] First launch triggers language selection prompt; onboarding conversation starts in
      selected language

---

## Phase 5 — Consolidation Pipeline

Off-session memory consolidation runs reliably after every disconnect.

- [ ] RunConsolidation wired to WebSocket disconnect event (async, non-blocking)
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
