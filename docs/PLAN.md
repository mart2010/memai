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
- [x] `EngagementLevel` IntEnum — states: unseen | mentioned | explored | integrated (ordered; explored absorbs the former practiced level)
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

---

## Phase 7 — Installation Wizard (`setup/` package)

Third independent package (own venv, own `pyproject.toml`), same layout convention as
`client/`/`server/`. Guides a fresh install end-to-end; see `CLAUDE.md` "Design
Constraints" for the voice-config scope this wizard sits outside of.

### Domain (`setup/src/memai_setup/domain/`)
- [x] Catalogue value objects: `VRAMEstimate`, `LLMCatalogueEntry`, `STTCatalogueEntry`,
      `WhisperModelEntry` (now with `recommended: bool`), `TTSCatalogueEntry` (now with
      `bundled: bool`), `TTSVoiceEntry`, `FitLevel`, `FitAssessment`
- [x] `assess_fit(vram, available_vram_gb, reserved_gb)` domain service — refactored
      2026-07-01 to take a plain `VRAMEstimate` + explicit `reserved_gb` instead of an
      `LLMCatalogueEntry` and a hardcoded module constant, so the same pure function now
      backs both `SelectLLM` (reserves `LLM_SELECTION_HEADROOM_GB` for STT+TTS) and
      `ResolveSTTEngine` (reserves `STT_SELECTION_TTS_HEADROOM_GB` + the already-chosen
      LLM's own VRAM footprint, looked up by `plan.llm_model_id`, rather than a flat guess)
- [x] `language_coverage.offered_languages(stt_entries, tts_entries)` — pure domain
      service: languages covered by at least one installable (`has_adapter`) STT engine
      AND at least one TTS engine
- [x] `InstallationPlan` aggregate (`domain/plan.py`) — accumulates wizard decisions;
      enforces the "topology locked after first install" invariant. Added
      `database_url: str` field (defaults to the same connection string shipped in
      `server/config/memai.example.toml`) since no wizard step collects a real one yet —
      see "Known gaps" below

### Use Cases (`setup/src/memai_setup/services/`)
- [x] Ports: `WizardPrompter` (now with `heading(title, lines)` alongside `info()` — a
      visually distinct section banner, deliberately separate so it can't be confused
      with a routine status line; `QuestionaryPrompter` renders it as a bordered block,
      `FakeWizardPrompter` records it separately from `info_messages`), `CatalogueRepository`,
      `GPUDetector`, `ExistingInstallDetector`, `ModelInstaller`, `ConfigWriter`,
      `SchemaRunner`, `HealthCheck` (`services/ports.py`)
- [x] `WizardStep` protocol — each wizard page is an independently unit-testable use case
- [x] All 10 steps now fully implemented, matching the original flow doc's numbering
      exactly: `ShowWelcome` (step 1 — rendered as one `heading()` banner, not a run of
      `info()` lines; briefly explains single-host vs. split-host up front so the SSH
      prerequisite bullet isn't unexplained jargon, and clarifies that bullet is
      split-host-only; PortAudio bullet scoped to "macOS/Linux client only — Windows
      wheels already bundle it"; lists every other prerequisite including ones nothing
      here can check: CUDA driver, SSH key auth), `SelectTopology` (2), `CheckPrerequisites`
      (3 — Postgres/pgvector/Ollama; **warn-and-confirm, not hard-block**: on failure, asks
      the user via `prompter.confirm(..., default=False)` whether to continue anyway,
      raising `WizardAborted` if they decline; see `services/errors.py` — caught at the CLI
      boundary for a clean exit instead of a raw traceback), `SelectLLM` (4-5),
      `SelectLanguages` (6 — offers `offered_languages()`, multi-select; prompt text now
      explicit that this covers "your main language plus any optional ones" together, and
      that *which one is primary* is chosen later, live, during the first conversation
      (onboarding) — not here), `ResolveSTTEngine` (7 — filters by `has_adapter`, Whisper
      model-size fit check reserving room for the chosen LLM), `ResolveTTSEngines` (8 — per
      language: single covering engine installs silently, multiple engines prompt for
      choice since voice variety is a stated goal, not just coverage; `bundled` engines need
      no download), `GenerateConfig` (9 — single-host also writes client config; split-host
      defers to a separate `--client` run), `SetupSchema` (10 — delegates to
      `SchemaRunner`), `RunHealthChecks` (11 — aggregates a list of `HealthCheck` results,
      post-install verification; deliberately overlaps with `CheckPrerequisites` on
      Postgres/Ollama — one is pre-flight "don't waste time," the other is post-install
      "did it actually work")
- [x] `RunInstallWizard` orchestrator (`services/run_wizard.py`) — runs steps in order,
      pre-fills + locks `InstallationPlan.topology` from `ExistingInstallDetector`

### Infrastructure (`setup/src/memai_setup/infrastructure/`)
- [x] `TomlCatalogueRepository` — parses packaged `catalogues/*.toml`
- [~] `NvidiaSmiGPUDetector` — implemented (CUDA only, returns `None` on failure, never
      raises); only exercised so far on the Windows dev workstation, which has no
      `nvidia-smi` — confirmed the `None` fallback path works, but the real `nvidia-smi`
      parsing path (`memory.total` CSV output) is **unverified against an actual GPU**.
      Needs a real run on the Ubuntu GPU server before trusting the fit hints it drives.
- [x] `QuestionaryPrompter`
- [x] `FileExistingInstallDetector` — gracefully falls back to a fresh run (prints a
      one-line note) when an existing config is found but can't be parsed yet, instead of
      crashing with `NotImplementedError` (found via real use — this dev workstation has
      a real client `memai.toml`, and the original stub crashed on it)
- [x] `TomlConfigWriter` — real implementation. **Found and fixed a real bug while
      building it**: server and client configs share the exact same `memai.toml` path
      (`platformdirs.user_config_dir("memai")`), so for single-host topology, writing one
      after the other would have silently clobbered the first's `[server]` section.
      Fixed by making both methods read-modify-write (merge) rather than overwrite.
      Verified against a scratch file — output confirmed both `[server]` (ws_port +
      log_dir) and the client's own `ws_port` key coexist correctly.
- [x] `PsycopgSchemaRunner` — real implementation, reads
      `server/migrations/001_initial_schema.sql` via a relative monorepo-sibling path
      (cross-package file read, not a Python import — `setup` still has no dependency on
      `server`'s code). **Found and fixed a real bug in the migration itself**: the SQL
      had no `IF NOT EXISTS` on any `CREATE TABLE`/`CREATE INDEX`, so it would have failed
      on any re-run — directly breaking the wizard's "fully re-runnable" goal, and
      equally broken for anyone re-running `psql -f` by hand. Fixed at the source (all
      `CREATE TABLE`/`CREATE INDEX` now `IF NOT EXISTS`); the seed `INSERT` was already
      `ON CONFLICT DO NOTHING`.
- [x] `OllamaModelInstaller` — `pull_llm` via `ollama pull` subprocess (low-risk,
      well-documented); `download_whisper_model`/`download_piper_voice` via
      `huggingface_hub` (`Systran/faster-whisper-{size}`, `rhasspy/piper-voices` — repo
      structure verified against real HF pages during the TTS/STT catalogue research).
      Network-dependent; not run for real in this session (no verification needed beyond
      import-checking — doesn't touch GPU or require this machine's Postgres/Ollama).
- [x] `health_checks.py` — `PostgresHealthCheck` (psycopg connect), `PgvectorExtensionHealthCheck`
      (queries `pg_extension` — distinct failure mode from "Postgres reachable": a
      reachable Postgres does not imply pgvector is installed on that host, and the
      migration's `CREATE EXTENSION IF NOT EXISTS vector` would otherwise fail confusingly
      later in `SetupSchema`), `OllamaHealthCheck` (HTTP ping to `/api/tags`),
      `ServerWebSocketHealthCheck` (TCP connect to the configured port — **not** the
      originally-envisioned "launch memai-server as a subprocess and verify STT/TTS
      actually load," which needs the GPU server's own venv and is deferred; this only
      catches "forgot to start the server"). All four verified on this machine to fail
      gracefully (no crash) when Postgres/Ollama/server aren't running — real success path
      still needs the GPU server.

### Catalogues (`setup/src/memai_setup/catalogues/*.toml`)
- [x] `stt_catalogue.toml` — expanded 2026-07-01 after surveying alternatives to
      faster-whisper (NVIDIA Parakeet/Canary: too narrow, 1-25 languages, Canary also
      CC-BY-NC-4.0; Vosk: CPU-first, lower accuracy — both excluded). Added
      `whisper-large-v3-turbo` as a `whisper_models` size (809M params, "way faster,
      minor quality degradation" vs large-v3's 1550M — zero new code, works with the
      existing `FasterWhisperSTTService` today) and `whisper.cpp` as a second `[[engines]]`
      entry — same ~99-language Whisper coverage as faster-whisper but broader hardware
      backends (CUDA/Vulkan/ROCm/Metal/CoreML/OpenVINO), relevant to CLAUDE.md's stated
      long-term ROCm/Metal goal. `STTCatalogueEntry.has_adapter: bool` added as a new
      domain field (whisper.cpp = `false`, no `WhisperCppSTTService` exists yet) — same
      "make it explicit, not prose" rationale as `LLMCatalogueEntry.reasoning`.
- [x] `llm_catalogue.toml` — expanded 2026-07-01 from 3 entries (all pulled ad hoc on the
      GPU workstation) to an 11-entry surveyed landscape spanning ~4-27 GB VRAM: Aya
      Expanse (recommended default), Llama 3.1 8B, Command R7B, Qwen2.5 7B/14B, Gemma 3
      4B/12B/27B, Mistral NeMo 12B, plus the two originals kept as cautionary examples
      (qwen3:14b reasoning-model, llama3.3 70B too-large — llama3.3's VRAM figure
      corrected to the empirical ~57 GB loaded footprint from project_known_issues,
      not just its ~43 GB download size). Language lists verified against each vendor's
      own docs where an explicit list exists; Gemma 3's 140+ languages represented via
      the `{"*"}` wildcard (same convention as STT's faster-whisper entry).
- [x] `LLMCatalogueEntry.reasoning: bool` — new domain field (was previously only prose
      in `description`); `SelectLLM` now structurally appends a "<think> block is spoken
      aloud" warning to every `reasoning=true` entry's choice label instead of relying on
      each catalogue entry's author to remember to write it in by hand.
- [x] `tts_catalogue.toml` — full real language lists verified 2026-07-01 (web search
      against Piper's `VOICES.md` and Kokoro's `VOICES.md`): Kokoro 8 languages, Piper 37
      languages. Together they cover 16/17 of Coqui XTTS v2's languages (only Korean
      missing) — see in-file comment and [[project_tts_license_conflict]] memory (kept as
      "deferred", not "resolved" — licensing may change, and multiple TTS engines is a
      stated goal for voice variety, not just coverage)
- [x] `domain/languages.py` — `LANGUAGE_NAMES` lookup + `format_language()` ("German
      (de)") for plain-language wizard prompts; catalogue TOML `languages` arrays stay
      as plain ISO codes (machine-readable), display formatting is a separate concern

### CLI (`setup/src/memai_setup/cli.py`)
- [x] `memai-setup` runs the full 10-step flow (`ShowWelcome` through `RunHealthChecks`,
      matching the original flow doc's numbering exactly) with all real infrastructure
      wired in; catches `WizardAborted` at the boundary for a clean `sys.exit(1)` instead
      of a raw traceback, prints the LLM selection at the end
- [ ] `--client` flow
- [ ] `--uninstall` flow

### Known gaps (deliberate, documented — not oversights)
- No wizard step collects real Postgres connection details (no "collect Postgres
  connection" step exists) — `InstallationPlan.database_url` always defaults to
  `postgresql://memai:changeme@localhost:5432/memai`. `GenerateConfig`/`SetupSchema`/the
  prerequisite and health checks all read this one field, so there's exactly one place
  to fix once such a step exists.
- `ServerWebSocketHealthCheck` checks "is something listening on the port," not "did the
  server actually start successfully" (no subprocess launch — see infrastructure notes
  above).
- `--client` and `--uninstall` CLI flags still raise `NotImplementedError`.

### Tests
- [x] `tests/unit/domain/` — `test_fit_assessment.py` (now parameterized on
      `reserved_gb`), `test_installation_plan.py`, `test_languages.py`,
      `test_language_coverage.py`
- [x] `tests/unit/services/` — one test module per step: `test_show_welcome_step.py`
      (asserts it renders as exactly one `heading()` call with zero `info()` lines;
      single-host/split-host explained before the SSH bullet; SSH bullet is
      split-host-scoped; PortAudio bullet mentions macOS/Linux + Windows exemption),
      `test_check_prerequisites_step.py` (all-pass no-prompt / fail-then-confirm-continue
      / fail-then-decline-raises-`WizardAborted`), `test_select_llm_step.py`,
      `test_select_languages_step.py` (captures the prompt text and asserts it mentions
      "main language" and "first conversation" — not just that a selection got stored),
      `test_resolve_stt_engine_step.py` (including a test that headroom correctly
      accounts for the already-chosen LLM), `test_resolve_tts_engines_step.py`,
      `test_generate_config_step.py`, `test_setup_schema_step.py`,
      `test_run_health_checks_step.py`
- [x] `tests/integration/test_toml_catalogue.py` — real TOML parsing checks
- [x] `tests/fakes/fakes.py` — `FakeGPUDetector`, `FakeCatalogueRepository`,
      `FakeWizardPrompter` (now supports `select_many_answers` as its own queue, fixing a
      bug where `select_many` incorrectly reused the `select_answers` queue; also now
      records `heading()` calls separately from `info_messages`),
      `FakeExistingInstallDetector`, `FakeModelInstaller`, `FakeConfigWriter`,
      `FakeSchemaRunner`, `FakeHealthCheck`

**Verified (Windows dev workstation, no GPU):** `uv sync`, `uv run pytest` (38/38
passing), `ruff check` clean, full `cli.py` import/wiring check (all 10 steps construct
correctly), real terminal rendering of `ShowWelcome`'s heading banner manually inspected,
`TomlConfigWriter` merge behavior verified against a scratch file, all four
real `HealthCheck` implementations verified to degrade gracefully (no crash) with nothing
running locally, schema file path resolution verified to find the real (now-idempotent)
migration file.

**Not yet verified — requires the GPU server:** `NvidiaSmiGPUDetector`'s actual
`nvidia-smi` output parsing; the full `SelectLLM`/`ResolveSTTEngine` fit-hint output
against real VRAM; `OllamaModelInstaller`'s real downloads/pulls; any `HealthCheck`'s
actual *success* path (only the failure/degradation path was verified here); the full
wizard run end-to-end.
