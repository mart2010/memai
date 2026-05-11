# PLAN.md — Memai Implementation Plan

## Status Legend
- `[ ]` not started
- `[~]` in progress
- `[x]` done

## Starting Point

A working real-time voice pipeline exists in `server/server.py` and `client/client.py`:
STT (faster-whisper) → LLM (ollama, streamed) → TTS (piper), connected over binary
WebSocket frames. The entire domain layer, use case layer, memory system, and test
infrastructure are still to be built. Existing pipeline logic will be extracted into
proper adapters during Phase 3.

---

## Phase 1 — Domain Layer

Pure Python. No imports from outer layers. Fully unit-testable in isolation.

### Value Objects & Enums
- [x] `Language` value object (IETF language code)
- [x] `CEFRLevel` enum (A1, A2, B1, B2, C1, C2)
- [x] `LanguageProficiency` value object (language, level, is_native)
- [x] `Speaker` enum (user, assistant)
- [x] `EngagementLevel` enum — states: mentioned | explored | practiced | integrated
      (transition rules removed — LLM assigns freely, no domain enforcement)
- [x] `MemoryType` enum (EPISODE, CONCEPT, PROCEDURE)

### Entities & Aggregates
- [x] `User` entity (id, primary_language: Language, proficiencies: list[LanguageProficiency])
- [x] `Turn` entity (timestamp as identity, speaker, content, language)
- [x] `LiveConversation` aggregate (runtime only, never persisted):
      started_at as identity, rolling window summary + recent turns + active persona id
- [x] `ConversationRecord` aggregate root (id, started_at, ended_at, worthiness,
      persona_snapshot, turns, consolidated flag)
      — invariants: ended_at as single source of truth; ≥1 Turn + ended to consolidate; immutable once consolidated
- [x] `Episode` entity (id, summary, happened_at, conversation_id, embedding: list[float] | None)
- [x] `Concept` entity (id, name, description, language, engagement_level, embedding)
- [x] `Procedure` entity (id, name, steps, language, engagement_level, embedding)
- [x] `MemoryBrief` singleton entity (content, generated_at)
- [x] `AssistantPersona` entity (id, name, system_prompt, is_system, created_at, updated_at)

### Domain Events
- [x] `PrimaryLanguageChanged` (user_id, old_language, new_language)
- [x] `RecallTriggered` (query: str, memory_types: tuple[MemoryType, ...])
- [x] `PersonaSwitched` (from_persona_id, to_persona_id)
- [x] `PersonaSuggested` (detected_language, suggested_persona_id)

### Domain-owned Protocols
- [x] `WorthinessEvaluator` Protocol (evaluate(record: ConversationRecord) → bool)
- [x] `RecallIntentDetector` Protocol (detect(text: str) → RecallTriggered | None)
- [x] `LanguageDetector` Protocol (detect(text: str) → Language)
- [x] `PersonaIntentDetector` Protocol (detect(text: str) → str | None)
- [x] `should_suggest_persona` domain function (replaces EngagementEvaluator — pure function)

### Unit Tests — Phase 1
- [x] `ConversationRecord` invariants (add_turn/consolidation guards, eligibility)
- [x] `AssistantPersona` guard (is_system cannot be modified)

---

## Phase 2 — Use Case Layer

Application logic. All infrastructure behind Protocols. Fake* for tests.

### Infrastructure Ports (defined here, implemented in Phase 3)
- [ ] `STTService` Protocol (transcribe(audio: bytes, language: Language) → str)
- [ ] `LLMService` Protocol (complete(messages, system_prompt) → AsyncIterator[str])
- [ ] `TTSService` Protocol (synthesise(text: str) → bytes)
- [ ] `EmbeddingService` Protocol (embed(text: str) → list[float])
- [ ] `UserRepository` Protocol
- [ ] `ConversationRepository` Protocol
- [ ] `MemoryRepository` Protocol (upsert + similarity_search per MemoryType)
- [ ] `PersonaRepository` Protocol
- [ ] `MemoryBriefRepository` Protocol
- [ ] `TurnLogger` Protocol (append_turn(session_id, turn, db_written: bool) → None)

### Fake Implementations (in tests/fakes/)
- [ ] `FakeSTTService`
- [ ] `FakeLLMService`
- [ ] `FakeTTSService`
- [ ] `FakeEmbeddingService`
- [ ] `FakeUserRepository`
- [ ] `FakeConversationRepository`
- [ ] `FakeMemoryRepository`
- [ ] `FakePersonaRepository`
- [ ] `FakeMemoryBriefRepository`
- [ ] `FakeTurnLogger`

### Use Cases — Interaction Context
- [ ] `StartSession` — load User + MemoryBrief, initialise LiveConversation with active
      persona; inject MemoryBrief as static system block
- [ ] `ProcessTurn` — detect language (LanguageDetector), detect recall intent
      (RecallIntentDetector), detect persona intent (PersonaIntentDetector), run
      STT→LLM→TTS pipeline, apply implicit persona suggestion rule, trigger rolling
      window summarisation when turn-count watermark reached (async, between turns),
      write WAL, persist Turn
- [ ] `EndSession` — close ConversationRecord, flush WAL

### Use Cases — Persona Context
- [ ] `CreatePersona` (guard: only when GeneralAssistant active)
- [ ] `ListPersonas`
- [ ] `EditPersona` (guard: not is_system)
- [ ] `RemovePersona` (guard: not is_system)
- [ ] `SwitchPersona` — fire PersonaSwitched; result announced aloud

### Use Cases — Memory Context
- [ ] `TriggerRecall` — embed query → similarity search filtered by memory_types →
      inject top-N results into current turn's LLM context
- [ ] `RunConsolidation` — process all unconsolidated ConversationRecords oldest-first;
      per record: extract Episodes/Concepts/Procedures via upsert pattern; commit all
      writes + consolidated flag in one DB transaction
- [ ] `GenerateMemoryBrief` — LLM condenses current memory state → overwrite MemoryBrief

### Use Cases — User Management
- [ ] `UpdatePrimaryLanguage` — update User.primary_language, fire PrimaryLanguageChanged,
      announce change aloud at session start

### Unit Tests — Phase 2
- [ ] `StartSession` — correct MemoryBrief injection, correct persona loaded
- [ ] `ProcessTurn` — recall path (RecallTriggered fired + context injected), implicit
      persona suggestion rule, rolling window trigger
- [ ] `EndSession` — ConversationRecord closed, WAL flushed
- [ ] `RunConsolidation` — worthy vs. unworthy record, upsert merge vs. insert new,
      atomicity (consolidated flag only set on full commit)
- [ ] `UpdatePrimaryLanguage` — event fired, announcement triggered

---

## Phase 3 — Infrastructure Adapters

One adapter at a time. Inner layers unchanged.

### Persistence (PostgreSQL + pgvector)
- [ ] DB schema: users, personas, conversation_records, turns, episodes, concepts,
      procedures, memory_brief — with pgvector extension and HNSW index on embedding columns
- [ ] `PostgresUserRepository`
- [ ] `PostgresConversationRepository`
- [ ] `PostgresMemoryRepository` (pgvector similarity_search)
- [ ] `PostgresPersonaRepository` — seed GeneralAssistant at DB init
- [ ] `PostgresMemoryBriefRepository`

### STT
- [ ] `FasterWhisperSTTService` — language hint from primary_language;
      reconfigures on PrimaryLanguageChanged

### LLM
- [ ] `OllamaLLMService` — streamed; system prompt language follows primary_language
- [ ] `OllamaWorthinessEvaluator`
- [ ] `OllamaEngagementEvaluator`
- [ ] `OllamaRecallIntentDetector`
- [ ] `OllamaPersonaIntentDetector`
- [ ] `OllamaConsolidationExtractor` (extracts Episodes/Concepts/Procedures from turns)

### TTS
- [ ] `PiperTTSService` — voice selected from primary_language;
      reconfigures on PrimaryLanguageChanged

### Embeddings
- [ ] `SentenceTransformerEmbeddingService` (multilingual-e5-large; runs as a subprocess
      or lightweight separate process on the Ubuntu server)

### Language Detection
- [ ] `LangdetectLanguageDetector`

### WAL
- [ ] `JSONLTurnLogger` — appends to `logs/conversations/YYYY-MM-DD_<session_id>.jsonl`;
      line format: `{"ts": "…", "speaker": "…", "content": "…", "db_written": false}`
- [ ] `WALReplayer` — on server start: scan flat files for db_written: false entries,
      replay into DB before accepting connections

### Integration Tests — Phase 3
- [ ] PostgreSQL repositories (real DB, test schema)
- [ ] FasterWhisperSTTService (real model, short audio fixture)
- [ ] PiperTTSService (real model, short text fixture)
- [ ] SentenceTransformerEmbeddingService (real model, similarity sanity check)

---

## Phase 4 — Pipeline Tuning + WebSocket Layer

### 4a — Pipeline Tuning (before refactor)

Establish a smooth, GPU-accelerated baseline on the existing monolithic server.py before
any architectural changes. Do not refactor yet — validate performance first.

- [ ] Switch faster-whisper to CUDA device (float16 or int8 on GPU); confirm GPU is used
      via nvidia-smi during transcription
- [ ] Benchmark STT latency: measure time from end_utterance to first LLM token; target
      is perceptibly snappy (< ~1s for typical short utterance)
- [ ] Verify piper TTS is GPU-accelerated (or CPU-bound by design — confirm either way);
      tune if meaningful gain available
- [ ] End-to-end latency check: speak → first audio chunk back; identify dominant
      bottleneck (STT / first LLM token / first TTS sentence)
- [ ] Confirm smooth playback with no audio glitches or buffer underruns

Only proceed to 4b once the pipeline feels responsive and GPU utilisation is confirmed.

### 4b — WebSocket Layer (refactor)

Wire client ↔ server into Clean Architecture. All domain logic already tested.

### Server Entrypoint (refactor server.py)
- [ ] On connect: run WALReplayer if unwritten entries exist; call StartSession
- [ ] Binary frames (audio) → buffer; `end_utterance` → ProcessTurn
- [ ] Stream synthesised audio as binary frames; send `speaking_end` JSON frame after
      final chunk of each response
- [ ] On disconnect: call EndSession → trigger RunConsolidation (async, non-blocking)
- [ ] Handle `set_language` JSON frame at connection time → UpdatePrimaryLanguage

### Client Entrypoint (refactor client.py)
- [ ] `--lang` CLI argument (defaults to `en`); send `set_language` frame before session
- [ ] Announce primary language change aloud when server confirms update
- [ ] Suppress VAD from playback start until `speaking_end` received (mic muting)
- [ ] Existing: sounddevice capture, webrtcvad, binary frames, SSH tunnel — keep as-is

### End-to-End Smoke Test
- [ ] Client connects, speaks a sentence, receives synthesised audio response
- [ ] `--lang` flag updates User entity on server and triggers announcement

---

## Phase 5 — Consolidation Pipeline

Off-session memory consolidation runs reliably after every disconnect.

- [ ] RunConsolidation wired to WebSocket disconnect event (async, non-blocking)
- [ ] Oldest-first processing of all unconsolidated ConversationRecords
- [ ] Per-conversation atomicity: Episodes + Concepts + Procedures + consolidated flag
      in one DB transaction
- [ ] Crash recovery: unconsolidated records reprocessed safely on next run
- [ ] Reconnect during active consolidation: new session starts immediately with last
      committed MemoryBrief (stale is acceptable)
- [ ] End-to-end test: disconnect → verify records consolidated + DB state correct

---

## Phase 6 — MemoryBrief Generation and Session Injection

The assistant has meaningful context from past conversations at every session start.

- [ ] GenerateMemoryBrief use case wired at end of each full consolidation run
- [ ] MemoryBrief overwritten (single record, always current)
- [ ] StartSession injects MemoryBrief content as static system-level block
- [ ] End-to-end test: two sessions; second session's LLM context contains summary of first
