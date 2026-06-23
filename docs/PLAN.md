# PLAN.md — Memai Implementation Plan

## Status Legend
- `[ ]` not started
- `[~]` in progress
- `[x]` done

## Starting Point

A working real-time voice pipeline exists in `server/server.py` and `client/client.py`:
STT (faster-whisper) → LLM (ollama, streamed) → TTS (Kokoro), connected over binary
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
- [x] `RecallIntentDetector` Protocol (detect(text: str) → RecallTriggered | None) —
      lives in `domain/protocols.py`
- [~] `WorthinessEvaluator` Protocol (evaluate(conversation: Conversation) → bool) — exists,
      but lives in `services/ports.py`, not `domain/protocols.py`; not actually domain-owned
- `PersonaIntentDetector` Protocol — does not exist; removed in favour of LLM self-report
  (`_strip_persona_prefix` inline in `ProcessTurn`), see Phase 3 LLM section note

### Unit Tests — Phase 1
- [x] `Conversation` invariants (add_turn/consolidation guards, eligibility)
- [x] `AssistantPersona` guard (is_system cannot be modified)

---

## Phase 2 — Service Layer

Application logic. All infrastructure behind Protocols. Fake* for tests.

### Infrastructure Ports (defined here, implemented in Phase 3)
- [x] `STTService` Protocol — `transcribe(audio: bytes) → tuple[str, Language]`. No
      `language_hint` param (language is always auto-detected by Whisper); the description
      here was stale, matching an out-of-sync `FakeSTTService` (see below) rather than the
      real protocol in `services/ports.py`
- [x] `LLMService` Protocol (complete(messages, system_prompt) → AsyncIterator[str])
- [x] `TTSService` Protocol (synthesise(text: str, voice: str) → bytes)
- [x] `EmbeddingService` Protocol (embed(text: str) → list[float])
- [x] `UserRepository` Protocol
- [x] `SessionLogReader` Protocol (get_previous() → SessionInfo | None; read_tail(session_id, max_turns) → list[Turn])
- [x] `SessionInfo` value object (session_id, ended_at, clean_exit: bool)
- [x] `ConversationRepository` Protocol
- [x] `MemoryRepository` Protocol (upsert + similarity_search per MemoryType; search returns
      `list[tuple[float, MemoryItem]]` with cosine similarity)
- [x] `PersonaRepository` Protocol
- [x] `MemoryBriefRepository` Protocol
- [x] `TurnLogger` Protocol (append(session_id, turn, marker: ConversationBoundaryType | None), close(session_id, ended_at, clean_exit))
- [x] `SessionReplayReader` Protocol — not previously listed; backs `TurnLogReplayer`
      (see Phase 3 note on its actual location)
- [x] `WorthinessEvaluator`, `DisambiguationEvaluator`, `MemorySynthesizer`,
      `ConsolidationExtractor` Protocols — not previously listed; all in `services/ports.py`,
      power the consolidation/merge pipeline (`ConsolidateMemory` in `services/memory.py`)

### Fake Implementations (in tests/fakes/)
- [~] `FakeSTTService` — still has a stale `language_hint: Language | None` parameter not
      present on the real `STTService` protocol; worth removing for consistency
- [x] `FakeLLMService`
- [x] `FakeTTSService`
- [x] `FakeEmbeddingService`
- [x] `FakeUserRepository`
- [x] `FakeSessionLogReader`
- [x] `FakeConversationRepository`
- [x] `FakeMemoryRepository`
- [x] `FakePersonaRepository`
- [x] `FakeMemoryBriefRepository`
- [x] `FakeSessionReplayReader`, `FakeRecallIntentDetector`, `FakeWorthinessEvaluator`,
      `FakeConsolidationExtractor` — not previously listed
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
- [x] `ConsolidateMemory` — process all unconsolidated Conversations oldest-first; per conversation: extract
      Episodes/Concepts/Procedures via upsert pattern; commit all writes + consolidated flag
      in one DB transaction
- [x] `GenerateMemoryBrief` — LLM condenses current memory state → overwrite MemoryBrief

### Services — User Management
- [x] `UpdatePrimaryLanguage` — update User.primary_language, fire PrimaryLanguageChanged

### Unit Tests — Phase 2
- [x] `StartSession` — correct MemoryBrief injection, correct persona loaded,
      tail injected within threshold / not injected beyond threshold
- [x] `ProcessTurn` — recall path (RecallTriggered fired + context injected),
      topic break / continuation markers, rolling window trigger
- [x] `EndSession` — TurnLogger closed with clean_exit=True
- [x] `ConsolidateMemory` — worthy vs. unworthy conversation, concepts always extracted,
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
- [x] `TurnLogReplayer` — lives in `services/replay.py` (a use case, not an infra adapter —
      misfiled under "Persistence" here; it orchestrates `ConversationRepository`/
      `PersonaRepository`/`SessionReplayReader` ports). Its JSONL-side counterpart,
      `JSONLSessionReplayReader` (implements `SessionReplayReader`), lives in
      `infrastructure/json_file.py` and was previously unmentioned. Replays unprocessed
      JSONL session files into the DB (creates
      Conversation + Turn records); triggered two ways:
      (1) **Primary** — idle timer after clean session close: if no new session opens within
          N minutes of the `session_closed` marker, fire TurnLogReplayer → ConsolidateMemory
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
- [x] `FasterWhisperSTTService` (`infrastructure/stt.py`) — auto-detects language (no forced
      language); takes only `audio: bytes` — no `language_hint` param exists on the real
      protocol or this adapter

### LLM
`infrastructure/llm/` is a package (`__init__.py`, `_common.py`, `ollama.py`,
`openrouter.py`), not the single `infrastructure/llm.py` file referenced below.
- [x] `OllamaLLMService` (`infrastructure/llm/ollama.py`) — async streaming via `ollama.AsyncClient`
- [x] `OllamaWorthinessEvaluator` (`infrastructure/llm/ollama.py`) — sync one-shot, YES/NO prompt
- [x] `OllamaRecallIntentDetector` (`infrastructure/llm/ollama.py`) — sync, JSON format mode
- [x] `OllamaConsolidationExtractor` (`infrastructure/llm/ollama.py`) — sync, JSON format mode;
      extracts Episodes/Concepts/Procedures; persona_id from conversation snapshot
      — `OllamaPersonaIntentDetector` removed: persona switching is LLM self-report only
        (`_strip_persona_prefix` inline in `ProcessTurn`); no domain protocol needed
- [x] `OllamaMemorySynthesizer`, `OllamaDisambiguationEvaluator` (`infrastructure/llm/ollama.py`)
      — not previously listed; back the merge/synthesis path in `ConsolidateMemory`
- [x] Full `OpenRouter*` adapter family (`infrastructure/llm/openrouter.py`) — not previously
      listed at all: `OpenRouterLLMService`, `OpenRouterWorthinessEvaluator`,
      `OpenRouterRecallIntentDetector`, `OpenRouterConsolidationExtractor`,
      `OpenRouterMemorySynthesizer`, `OpenRouterDisambiguationEvaluator` — OpenAI-compatible
      client against openrouter.ai; a less-private, cloud-gateway alternative to the
      fully-local Ollama family, for users willing to trade privacy for capability/cost

### TTS
- [x] `KokoroTTSService` (`infrastructure/tts.py`) — Kokoro (Apache-2.0); lazily initialises
      one `KPipeline` per language prefix (cached); resamples 24 kHz → 16 kHz via `resample_poly`;
      voice selected per-persona via `AssistantPersona.tts_voice`

### Embeddings
- [x] `SentenceTransformerEmbeddingService` (`infrastructure/embedding.py`) —
      `intfloat/multilingual-e5-large`, 1024-dim, L2-normalised

### Similarity threshold & merge logic
- [x] Merge logic implemented as `_merge_action`/`_existing_to_merge` in `services/memory.py`
      (renamed from the `_cosine_similarity`/`_should_merge` this section originally referred
      to) — consumes the similarity score returned directly by `MemoryRepository.search()`
      (pgvector `embedding <=> %s AS distance` in `PSMemoryRepository`); no redundant manual
      cosine computation exists. Two-tier threshold per `CLAUDE.md`'s documented design:
      `_MERGE_THRESHOLD = 0.93` (auto-merge), `_DISAMBIGUATE_THRESHOLD = 0.75` (LLM
      disambiguation), below that auto-insert.
- [ ] Both thresholds are still hardcoded module-level constants in `memory.py` — replace
      with a global config value once real usage data exists to calibrate against (still the
      critical open tuning parameter this section originally flagged)

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

- [x] Wire `StartSession`, `ProcessTurn`, `EndSession` into a real WebSocket handler
- [x] Real services: `FasterWhisperSTTService` (CUDA float16), `OllamaLLMService`, `KokoroTTSService`
- [x] In-memory stubs: fixed `User` (primary_language = "en"), fixed `GeneralAssistant` persona
      (response_language = "en", tts_voice = "af_heart"), no-op `TurnLogger`
- [x] Verify GPU is active for STT (nvidia-smi during transcription) — required fixing
      `LD_LIBRARY_PATH` (see findings below); confirmed via `nvidia-smi` GPU utilization
- [ ] Benchmark STT latency: time from `end_utterance` to first LLM token; target < ~1s
- [ ] Benchmark TTS latency: time from first complete LLM sentence to first audio chunk
- [x] End-to-end: speak → first audio chunk back; dominant bottleneck identified as LLM
      model fit (see findings below) — precise STT/TTS latency numbers still not benchmarked
- [x] Confirm smooth playback, no audio glitches or buffer underruns
- [x] Verify Kokoro voice names match the installed version (see `KOKORO_DEFAULT_VOICES` in `infrastructure/tts.py`)

**Findings from first live test on the GPU server (RTX 4090, 24 GB VRAM):**
- `.env` is never actually loaded by the app (`python-dotenv` is a declared dependency but
  `load_dotenv()` is never called) — must be sourced manually (`set -a && source .env && set +a`)
  before starting `memai-server`, otherwise `LD_LIBRARY_PATH` is missing and faster-whisper
  fails with `libcublas.so.12 not found`. Added `load_dotenv()` to `main()` in `server.py`
  (uncommitted) — **not yet verified on the GPU server**:
  - [ ] Confirm `load_dotenv()` inside `main()` actually fixes the `libcublas.so.12` error.
        Risk: glibc's dynamic linker may cache its `LD_LIBRARY_PATH` search path at process
        start (before `main()` runs), so setting it via `os.environ` from inside the already-
        running Python process could be too late for a later `dlopen()` triggered when
        `ctranslate2` loads. If it doesn't work, fall back to documenting manual sourcing
        (or wrap the entry point in a small shell script that sources `.env` before exec'ing
        Python) rather than relying on in-process `load_dotenv()`.
- `llama3.3` (70B) does not fit in 24 GB VRAM alongside Whisper + Kokoro — Ollama silently
  splits it 65%/35% CPU/GPU and evicts it after ~5 min idle, causing 30s+ cold-reload stalls
  with no error logged (looked like total silence on the client). `qwen3:14b` fits VRAM but
  is a reasoning model — emits `<think>` blocks that get spoken aloud; `think:false` does not
  suppress this. Settled on `aya-expanse` (~8B, multilingual, no reasoning overhead) as the
  default `LLM_MODEL` — see `docs/INSTALL_SERVER.md` and `CLAUDE.md`.
- Added two TTS-side sanitization passes in `ProcessTurn` (`_strip_markdown`,
  `_spell_out_numbers` in `services/session.py`) — LLMs reliably ignore "don't use markdown"
  instructions, and Kokoro/espeak's native number-reading is inconsistent outside English.
  Both are deterministic post-processing, not prompt-reliant.
- Implemented the onboarding redesign (previously an open issue): `GeneralAssistant` renamed
  to **Memai**; `ONBOARDING_SCRIPT` + first-launch directive added in `services/session.py`
  so the assistant introduces itself, explains voice-only configuration, and can replay the
  intro on request — no separate detector needed, handled by the main LLM call.

Only proceed to Pass 2 once STT/TTS latency is actually benchmarked on GPU.

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
      N minutes, fire `TurnLogReplayer` → `ConsolidateMemory` → `GenerateMemoryBrief` (all async,
      non-blocking); cancel timer on new connection

#### Real repositories
- [ ] `PSUserRepository`, `PSPersonaRepository`, `PSConversationRepository`,
      `PSMemoryRepository`, `PSMemoryBriefRepository`
- [ ] `JSONLTurnLogger` (live path)
- [ ] `TurnLogReplayer` (crash recovery on startup + idle timer trigger post-disconnect)
- [ ] `ConsolidateMemory` + `GenerateMemoryBrief` (triggered by idle timer)
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

- [ ] Full offline pipeline wired: TurnLogReplayer → ConsolidateMemory → GenerateMemoryBrief,
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
