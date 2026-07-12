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
- [x] `SUPPORTED_LANGUAGES` constant — intersection of faster-whisper and Kokoro (~7 languages
      as of 2026-07-09 — Korean was removed, see Phase 3 findings; Kokoro is the limiting factor)
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
- [x] `WorthinessEvaluator` Protocol (evaluate(conversation: Conversation) → bool) — moved
      from `services/ports.py` to `domain/protocols.py`; `ConsolidateMemory` now imports it
      from there
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
- [x] `FakeSTTService` — matches the real `STTService` protocol (`transcribe(audio: bytes)`
      only); the stale `language_hint` param this item used to flag was removed during
      Phase 4 Pass 1's test/fake-drift cleanup (see that section's findings) — this
      checkbox was just never updated to reflect it
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
- [x] `PSUnitOfWork` (`infrastructure/postgres.py`) — wraps the shared autocommit
      connection in `conn.transaction()` for a block; backs `ConsolidateMemory`'s
      per-conversation atomicity (see Phase 5). `UnitOfWork` Protocol lives in
      `services/ports.py`; `FakeUnitOfWork` (no-op context manager) in `tests/fakes/`

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
      merge_threshold = 0.93 (auto-merge), disambiguate_threshold = 0.75 (LLM
      disambiguation), below that auto-insert.
- [x] Thresholds externalised to config: new `[memory]` section in `memai.toml`
      (`merge_threshold`, `disambiguate_threshold`, `infrastructure/config.py`'s
      `ServerConfig`), passed into `ConsolidateMemory.__init__` and threaded through
      `_merge_action`/`_existing_to_merge` as explicit params instead of module constants
      (`DEFAULT_MERGE_THRESHOLD`/`DEFAULT_DISAMBIGUATE_THRESHOLD` remain as constructor
      defaults for callers/tests that don't care). Values themselves (0.93/0.75) are still
      placeholders pending calibration against real usage data — that calibration is the
      Phase 3 Integration Test below, still blocked on real DB/embedding access.

### Integration Tests — Phase 3
- [x] PostgreSQL repositories (real DB, test schema) — `server/tests/integration/test_postgres.py`
      (2026-07-08), against a dedicated `memai_test` database (never the dev DB) created/migrated
      fresh each session, tables truncated per test. Covers all five `PS*Repository` classes +
      `PSUnitOfWork`: user/persona/conversation/memory-item CRUD round trips, `get_unconsolidated`
      ordering, persona-scoped concept/procedure search (the "big bang" astronomy-vs-sitcom
      disambiguation example from CLAUDE.md), `conversations.persona_id` `ON DELETE RESTRICT`,
      `concepts`/`procedures` `ON DELETE CASCADE` on persona delete, `PSUnitOfWork` commit/rollback.
      23/23 passing live.
- [x] `FasterWhisperSTTService` (real model, short audio fixture) — `server/tests/integration/test_stt.py`,
      real speech synthesized via `espeak-ng` (already a system dependency for Kokoro's non-English
      backend — no binary fixture committed) and transcribed for real on CUDA. 2/2 passing live
      (needed `MEMAI_TEST_WHISPER_MODEL_PATH` pointed at the already-cached local model directory —
      the bare model-name default tries to download from HF, blocked without the corporate-proxy
      env vars).
- [x] `KokoroTTSService` (real model, short text fixture; verify default voice names match installed
      version) — `server/tests/integration/test_tts.py`. 4/4 core tests passing (real synthesis,
      `speaking_rate` affecting output length); per-language default-voice check parametrized over
      all of `KOKORO_DEFAULT_VOICES`, skipping (not failing) languages whose voice pack/optional
      dependency isn't installed locally (es/it/pt/ja/zh-cn — matches the known Phase 7 TODO on
      wizard-driven model downloads). **Real bug found and resolved 2026-07-09**: the `ko` entry
      in `KOKORO_DEFAULT_VOICES`/`_PREFIX_TO_LANG` (`infrastructure/tts.py`) never worked at all —
      the installed Kokoro package (`hexgrad/Kokoro-82M`) has no Korean pipeline; its only valid
      `lang_code`s are `a/b/e/f/h/i/p/j/z` (English×2, Spanish, French, **Hindi**, Italian,
      Portuguese, Japanese, Mandarin). Considered fixing via a second TTS engine (MeloTTS, MIT
      licensed, has a real Korean model) but rejected: MeloTTS's actual dependency footprint is
      far heavier than expected for a single-language fix — full Japanese pipeline (`unidic`,
      `mecab-python3`, `fugashi`), full Chinese pipeline (`pypinyin`, `jieba`), `gradio`/
      `tensorboard` (its own demo webapp), and its own `torch`/`torchaudio` pins with real risk of
      reopening the `nvidia-cublas` CUDA-version conflict this project already fixed once (Phase 4
      Pass 2). No proper PyPI package either — official install is git-clone + `pip install -e .`
      + a manual `python -m unidic download` step, the same fragile pattern as the earlier spaCy
      saga. **Decision: dropped Korean from `SUPPORTED_LANGUAGES` instead of adding MeloTTS.**
      `ko` removed from `SUPPORTED_LANGUAGES` (`domain/model.py`) and from
      `KOKORO_DEFAULT_VOICES`/`_PREFIX_TO_LANG` (`infrastructure/tts.py`); ~7 languages supported
      now. Not affected: the wizard's own language offering (`SelectLanguages` step,
      `offered_languages()`) was already correctly excluding Korean before this fix, since neither
      Kokoro's nor Piper's catalogue entries ever listed `ko` — confirmed live during the Phase 7
      wizard run (2026-07-09), whose printed language list had no Korean option. LLM catalogue
      entries correctly keep `ko` in their own `languages` lists (a model understanding Korean
      text is unrelated to Memai's TTS/STT voice coverage). MeloTTS not ruled out permanently —
      revisit if a lighter-weight Korean-only TTS option turns up later.
- [x] `SentenceTransformerEmbeddingService` — real model, calibration test: embed pairs
      of semantically similar vs. dissimilar texts and print similarity scores to help
      determine a good threshold value for the merge decision — `server/tests/integration/test_embedding.py`,
      7/7 passing live. Real numbers from the GPU workstation (2026-07-08, run with `-s`):
      similar pairs 0.87–0.98 (paraphrase 0.98, cross-lingual FR/EN paraphrase 0.93, hypernym
      "golden retriever"/"dog" 0.87), dissimilar pairs 0.71–0.86 ("big bang" astronomy vs sitcom
      0.86 — a real near-miss for the disambiguation band, not a clean auto-insert case; unrelated
      sentences 0.71). Confirms the `0.75`/`0.93` two-tier thresholds in CLAUDE.md are in a
      plausible range but the CONCEPT/CONCEPT hypernym vs. same-name-different-domain cases sit
      close together (0.86–0.87) — worth revisiting once more real usage data accumulates, per
      CLAUDE.md's "still placeholders pending calibration" note.

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
- [x] Benchmark STT latency: time from `end_utterance` to first LLM token; target < ~1s
      — **STT itself: 0.05–0.4s, comfortably under target.** See full findings below for
      the LLM/TTS side of "first token" latency, which needed two real bugs fixed first.
- [x] Benchmark TTS latency: time from first complete LLM sentence to first audio chunk
      — **0.04–0.08s once Kokoro's per-language pipeline is warmed (one-time ~2.1–2.6s
      init cost on first use of a given language per process)**
- [x] End-to-end: speak → first audio chunk back; steady-state **total time-to-first-audio
      is well under 1s** after the fixes below (was previously bimodal 0.2s–2.7s, sometimes
      up to 9s+ on cold start)
- [x] Confirm smooth playback, no audio glitches or buffer underruns
- [x] Verify Kokoro voice names match the installed version (see `KOKORO_DEFAULT_VOICES` in `infrastructure/tts.py`)

**Findings from first live test on the GPU server (RTX 4090, 24 GB VRAM):**
- The `.env`/`load_dotenv()` concern below is now **moot** — superseded by the
  `infrastructure/config.py` TOML config refactor (no more `python-dotenv` dependency at
  all). `LD_LIBRARY_PATH`/`SSL_CERT_FILE` remain OS/dynamic-linker concerns that must be set
  in the process launch environment (shell or systemd unit), not inside the app — confirmed
  working when set this way; the original "confirm inside `main()`" plan doesn't apply since
  there's no more in-process env-loading step at all.
- `llama3.3` (70B) does not fit in 24 GB VRAM alongside Whisper + Kokoro — Ollama silently
  splits it 65%/35% CPU/GPU and evicts it after ~5 min idle, causing 30s+ cold-reload stalls
  with no error logged (looked like total silence on the client). `qwen3:14b` fits VRAM but
  is a reasoning model — emits `<think>` blocks that get spoken aloud; `think:false` does not
  suppress this. Settled on `aya-expanse` (~8B, multilingual, no reasoning overhead) as the
  default `LLM_MODEL` — see `docs/INSTALL_SERVER.md` and `CLAUDE.md`. (This GPU box is a
  shared lab machine with many other Ollama models already pulled; `aya-expanse` itself
  wasn't yet pulled there and outbound `ollama pull` is blocked by the corporate proxy — see
  proxy finding below. Benchmarks below used the already-available `gemma4` (8B, Q4_K_M) as
  a stand-in; re-run once `aya-expanse` is actually pulled.)
- Added two TTS-side sanitization passes in `ProcessTurn` (`_strip_markdown`,
  `_spell_out_numbers` in `services/session.py`) — LLMs reliably ignore "don't use markdown"
  instructions, and Kokoro/espeak's native number-reading is inconsistent outside English.
  Both are deterministic post-processing, not prompt-reliant.
- Implemented the onboarding redesign (previously an open issue): `ONBOARDING_SCRIPT` +
  first-launch directive added in `services/session.py` so the assistant introduces itself,
  explains voice-only configuration, and can replay the intro on request — no separate
  detector needed, handled by the main LLM call. Pass 1 had also renamed `GeneralAssistant`'s
  spoken name to **Memai** — **reverted 2026-07-06**: Memai is the product/project name, not
  the assistant's own spoken identity, and conflating the two was a mistake. The persona's
  default name is now the generic placeholder **"Vocal Assistant"** (see `domain/model.py`'s
  `general_assistant()` factory and the migration seed in `001_initial_schema.sql`). Ideally
  this name becomes voice-configurable later, same as `primary_language` — deferred for now
  since `AssistantPersona.update()` currently blocks any name/system_prompt change on
  `is_system` personas, which would need relaxing (name only) to support it.
- **Fixed: `ProcessTurn.execute` wasn't actually streaming.** The original code drained the
  entire LLM token stream into one string before doing any sentence-splitting or TTS, so the
  user waited for the *full* reply before hearing a word — despite `CLAUDE.md` describing the
  pipeline as token-streamed/sentence-by-sentence TTS. Rewrote it to resolve an optional
  `[PERSONA:name]`/boundary-marker prefix incrementally as tokens arrive, then synthesise each
  sentence via TTS as soon as it completes, while later tokens are still streaming in. See
  `_try_resolve_prefixes`/`_resolve_boundary_marker` in `services/session.py`.
- **Fixed: Ollama's default 5-minute `keep_alive` evicted the model between conversational
  turns**, causing multi-second cold-reload spikes mid-conversation. `OllamaLLMService.complete()`
  now passes `keep_alive="30m"` explicitly (scoped to just the live conversational path, not
  the offline consolidation LLM calls) — see `infrastructure/llm/ollama.py`.
- **Fixed: corporate-proxy env vars were breaking real LLM streaming.** This GPU workstation
  sits behind a corporate egress proxy (SSL-inspecting, blocks direct outbound HTTPS). Setting
  `http_proxy`/`https_proxy` on the whole `memai-server` process (to work around a blocked
  spaCy model download — see next item) inadvertently routed `OllamaLLMService`'s calls to
  `localhost:11434` through that proxy too, since env-var-based proxy config applies to *all*
  outbound HTTP from a process unless `NO_PROXY` explicitly excludes hosts. The proxy doesn't
  pass through chunked/streamed responses incrementally — it buffers the whole response before
  releasing it — so "time to first token" was silently measuring *total generation time*
  instead. **Also a genuine privacy concern**: conversation content (STT transcripts, LLM
  prompts/completions) was being routed through a corporate inspection proxy even though it
  never needed to leave the machine, undermining the project's fully-local design goal.
  **Fix**: the live server process must never have proxy env vars set at all — see next
  finding for why it no longer needs to.
- **Fixed: Kokoro's English G2P (`misaki.en`) lazily auto-downloads a spaCy model
  (`en_core_web_sm`) on first use**, which both needs network access (blocked without the
  proxy) and — separately — spaCy's own `download()` can't find `pip`/`uv` inside a
  `uv`-managed venv (which deliberately doesn't bundle `pip`), so it would fail even with
  network access fixed. Resolved by installing it as a proper pinned dependency instead of
  relying on spaCy's downloader: `uv add "en_core_web_sm @ <github wheel URL>"` (see
  `[tool.uv.sources]` in `server/pyproject.toml`). The live server now never needs network
  access for TTS at all, for any supported language (French/etc. always used the
  `espeak.py`/espeak-ng backend, never spaCy, and were unaffected).
- **Fixed several instances of test/fake ↔ real-protocol drift** that were silently breaking
  `pytest` collection or making every `ProcessTurn` test fail: `tests/fakes/fakes.py` imported
  `WorthinessEvaluator` from the wrong module, `FakeSTTService.transcribe()` had a stale
  `language_hint` param not on the real `STTService` protocol, `FakeTurnLogger.append()` was
  missing the real `persona_id` param, and two test files (`test_consolidation.py`,
  `test_persona.py`) imported since-renamed classes (`RunConsolidation`→`ConsolidateMemory`,
  `SessionContext`→`WorkingMemory`). Also fixed a stale assertion in `test_persona.py`
  expecting the old `"General Assistant"` name instead of `"Memai"`. Full suite (82 tests)
  passes clean now.
- **Fixed: a single connection's unhandled exception could kill the entire server process**,
  not just that connection — `process_turn.execute()`'s exceptions weren't caught by the
  `except websockets.exceptions.ConnectionClosed` clause in `server.py`'s handler, so any bug
  mid-turn (e.g. the spaCy crash above) took down every other connection too. Now wrapped in
  its own `try/except`, logs via `traceback.print_exc()`, and lets the session continue.
- **Fixed: Pass-1 `_PersonaRepo` stub ignored the already-known `primary_language` for
  returning users.** `_UserRepo` read `cfg.primary_language` correctly, but `_PersonaRepo`
  always constructed the persona with hardcoded English defaults — only the live
  `language_selected` onboarding handler set `response_language`/`tts_voice` correctly. So any
  session that skipped onboarding (because a language was already saved) got a language/voice
  mismatch. `_PersonaRepo` now takes `primary_language` and derives both correctly.
- Deleted `server/.env` (both on the GPU box and this laptop's checkout) — dead since the TOML
  config refactor, the app no longer reads it at all.

Pass 1 latency benchmarking is done; Pass 2 wiring can proceed.

### Pass 2 — Full wiring

Swap in real repositories and wire the offline consolidation pipeline.

#### Server Entrypoint
- [x] On connect: run `TurnLogReplayer` if unwritten entries exist; check `User.primary_language`
      — replay runs unconditionally at the top of every connection (`_replay_unprocessed_sessions`
      in `server.py`); idempotent (no-op when nothing unprocessed), so this single call also
      covers crash recovery on server restart without a separate startup-only code path
- [x] If `primary_language` is None: send `select_language` with `SUPPORTED_LANGUAGES` list;
      await `language_selected` frame; then start onboarding session — uses `CompleteOnboarding`
      (`services/user.py`) rather than hand-rolling a new `User`/persona mutation inline, as
      Pass 1's stub did
- [x] Normal session: call `StartSession` (injects MemoryBrief + session tail if applicable)
- [x] Binary frames (audio) → buffer; `end_utterance` → `ProcessTurn`
- [x] Stream synthesised audio as binary frames; send `speaking_end` JSON frame after
      final chunk of each response
- [x] On disconnect: call `EndSession`; start idle timer — if no new session opens within
      N minutes, fire `TurnLogReplayer` → `ConsolidateMemory` → `GenerateMemoryBrief` (all async,
      non-blocking); cancel timer on new connection — `idle_consolidation_minutes` (new
      `[server]` config field, default 5.0) controls N; timer tracked on `ServerContext.idle_timer_task`

#### Real repositories
- [x] `PSUserRepository`, `PSPersonaRepository`, `PSConversationRepository`,
      `PSMemoryRepository`, `PSMemoryBriefRepository` — all wired into `server.py`'s
      `ServerContext`, replacing every Pass 1 in-memory stub
- [x] `JSONLTurnLogger` (live path) — unchanged from Pass 1, still the only write path during
      a live conversation (see Live/Offline boundary in `CLAUDE.md`)
- [x] `TurnLogReplayer` (crash recovery on startup + idle timer trigger post-disconnect) —
      both triggers now call the same `_replay_unprocessed_sessions` helper
- [x] `ConsolidateMemory` + `GenerateMemoryBrief` (triggered by idle timer) — offline LLM
      adapters (`OllamaWorthinessEvaluator`, `OllamaDisambiguationEvaluator`,
      `OllamaMemorySynthesizer`, `OllamaConsolidationExtractor`) and
      `SentenceTransformerEmbeddingService` instantiated once at server startup, reused for
      both consolidation and live `TriggerRecall`-style embedding
- [x] DB pre-requisite: run `001_initial_schema.sql` (still a manual/wizard step — unchanged).
      Inserting the User record is **no longer a manual step**: `_ensure_user_exists()` in
      `server.py` bootstraps the singleton `User` row automatically on first startup if
      missing, since this is a single-user system with no auth — simpler and less error-prone
      than requiring a hand-run `INSERT` before first connect
- [x] `postgres.connect()` now opens with `autocommit=True` (previously unset — every write
      was silently left in an uncommitted transaction). Simple and correct for this
      single-connection, single-user process; Phase 5's "per-conversation atomicity"
      requirement for `ConsolidateMemory` should wrap that call in an explicit
      `with conn.transaction():` block later, which composes fine on top of autocommit
- [x] **Design decision**: `User.primary_language` is now DB-only (via `UserRepository`),
      dropping the Pass 1 TOML `voice_configurable.primary_language` mirror
      (`ServerConfig.primary_language` field and the `update_voice_config()` call removed).
      It remains conceptually voice-configurable (set during onboarding, changeable later by
      voice) — only the persistence mechanism moved from config-file to Postgres, avoiding a
      dual-write/drift risk now that the DB is wired for real. `memai.example.toml`'s
      `[voice_configurable]` section has a comment noting this and reserving the section for
      future settings without their own domain entity (e.g. `llm_temperature`)

#### Client Entrypoint (refactor client.py)
- [x] On connect: if server sends `select_language`, render `questionary` terminal dropdown
      with the supported language list; send `language_selected` result — already implemented,
      no changes needed
- [x] Suppress VAD from playback start until `speaking_end` received (mic muting) — already
      implemented via `_mic_active` threading.Event, no changes needed
- [x] Existing: sounddevice capture, webrtcvad, binary frames, SSH tunnel — kept as-is

**Verified live on the GPU workstation (2026-07-06)** — real Postgres (Docker), real
STT/LLM/TTS/embedding models, full WebSocket round-trip: connect → onboarding skip (user
already had `primary_language` set) → audio → STT → LLM (`gemma4`) → TTS (Kokoro) → 11 audio
chunks streamed back → `speaking_end` → disconnect → idle timer → `TurnLogReplayer` →
`ConsolidateMemory` (extracted a real `Concept`) → `GenerateMemoryBrief`, all confirmed by
querying the DB directly afterward. `_ensure_user_exists` bootstrap and crash-recovery replay
(`TurnLogReplayer` running on every connect) both confirmed working. `ruff check` clean on all
changed files.

**Real bugs found and fixed along the way** (environment/infra, not Pass 2 logic bugs):
- **`nvidia-cublas` CUDA major-version conflict**: adding `SentenceTransformerEmbeddingService`
  (via `torch`) as a live dependency pulled in `nvidia-cublas` 13.x, but `ctranslate2`
  (faster-whisper) needs `libcublas.so.12` specifically, and no CUDA-12 cublas package ended up
  installed anywhere in the venv — STT crashed with `Library libcublas.so.12 is not found`.
  Pass 1 never hit this since it had no `torch` dependency at all. Worked around at the time via
  `LD_LIBRARY_PATH` pointing at Ollama's bundled CUDA-12 libs (`/usr/local/lib/ollama/cuda_v12`)
  — not durable, depended on Ollama's install being present.
  **Fixed properly**: `nvidia-cublas-cu12` pinned explicitly in `server/pyproject.toml`.
  Confirmed via `uv.lock` inspection that this is a real gap, not a resolver conflict —
  `ctranslate2` declares zero CUDA dependency of its own (`numpy`, `pyyaml`, `setuptools`
  only) and `torch` pulls the differently-named, CUDA-13-generation `nvidia-cublas` package
  on Linux, so nothing in the tree provided `libcublas.so.12` without this pin.
  **Verified live 2026-07-08**: `uv sync` + real `WhisperModel(..., device="cuda")` load and
  transcribe call succeeded on the GPU workstation with no `LD_LIBRARY_PATH` workaround needed.
- **Fixed: `SentenceTransformerEmbeddingService` needed network on every load, even
  fully-cached.** `SentenceTransformer(...)` does a HEAD request to Hugging Face Hub to check
  for updates regardless of local cache state — same "live server must never need network"
  violation as the old spaCy/en_core_web_sm issue. Fixed by setting `HF_HUB_OFFLINE=1` at
  import time in `infrastructure/embedding.py` (before `sentence_transformers` is imported) —
  same principle applies to Kokoro's voice-pack loading (`hf_hub_download` in
  `kokoro/pipeline.py`), which inherits the same env var from the same process.
  `intfloat/multilingual-e5-large` and Kokoro's `af_heart`/`ff_siwis` voice packs must be
  pre-downloaded into the HF cache before first live run (same pattern as Whisper models).
- **Found: one-off/manual DB scripts using plain `psycopg.connect()` (not `postgres.connect()`)
  default to `autocommit=False`** — an `UPDATE` run this way without an explicit `.commit()`
  leaves an uncommitted transaction. Didn't actually block anything here (Postgres rolls back
  on connection close), but worth remembering when poking at the DB by hand outside the app.
- **Self-inflicted test artifact, not a real bug, but a real design risk it exposed**: testing
  with an aggressively short `idle_consolidation_minutes` (0.05) caused new connections to hang
  indefinitely waiting for `select_language`/audio responses, because `ConsolidateMemory`'s
  extraction/embedding/synthesis calls are fully synchronous (no `await`, no
  `asyncio.to_thread`) and block the single-threaded asyncio event loop for their entire
  duration. Pass 2's wiring makes the offline pipeline a genuine background
  `asyncio.create_task` that can now overlap in wall-clock time with a live connection for the
  first time (Pass 1 had no such background task) — with the default 5-minute delay this is
  unlikely to bite in practice, but Phase 5's stated goal ("reconnect during active
  consolidation: new session starts immediately") is not actually true yet with synchronous
  blocking calls. Flagged for Phase 5, not fixed here.
- **Fixed: `_strip_markdown` in `services/session.py` only stripped emphasis markers
  (`**bold**`, `_italic_`, `` ` ``) — not headers (`#`), horizontal rules, or emoji.** A real
  `gemma4` response came back with `### 💾 Updated Profile Brief` — despite the system prompt
  explicitly forbidding markdown — and would have been read aloud by Kokoro largely
  unfiltered. Added `_MARKDOWN_HEADER`, `_MARKDOWN_HRULE`, and `_EMOJI` regexes alongside the
  existing `_MARKDOWN_EMPHASIS` one; covered by `tests/unit/services/test_markdown_stripping.py`.

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
- [x] Client connects, speaks a sentence, receives synthesised audio response — verified
      2026-07-06 via a scripted WebSocket test client (synthetic espeak-ng audio, not the real
      mic/`sounddevice` hardware client) against the real GPU-workstation server; 11 audio
      chunks received back
- [x] First launch triggers language selection prompt; onboarding conversation starts in
      selected language — verified in an earlier run this session (`select_language` sent,
      `language_selected` handled, `CompleteOnboarding` persisted to DB); not re-verified
      end-to-end with the real client hardware

---

## Phase 5 — Consolidation Pipeline

Off-session memory consolidation runs reliably after every disconnect.

- [x] Full offline pipeline wired: TurnLogReplayer → ConsolidateMemory → GenerateMemoryBrief,
      triggered by idle timer after clean disconnect — `_run_offline_pipeline`/
      `_run_offline_pipeline_after_idle` in `server.py` (Phase 4 Pass 2); live-verified
      2026-07-06
- [x] Oldest-first processing of all unconsolidated Conversations — `get_unconsolidated()`'s
      `ORDER BY c.started_at, t.timestamp` in `PSConversationRepository`
- [x] Per-conversation atomicity: Episodes + Concepts + Procedures + consolidated flag
      in one DB transaction — added `UnitOfWork` port (`services/ports.py`) + `PSUnitOfWork`
      (`infrastructure/postgres.py`), wraps each conversation's body in `ConsolidateMemory.execute`
      in a `conn.transaction()` block; `FakeUnitOfWork` (no-op) for unit tests. Closes the gap
      `postgres.connect()`'s docstring used to flag
- [x] Crash recovery: unconsolidated Conversations reprocessed safely on next run — guaranteed
      now by the atomicity fix above (a failed conversation commits nothing, so it's retried in
      full) plus `TurnLogReplayer`'s existing idempotency
- [x] Reconnect during active consolidation: new session starts immediately with last
      committed MemoryBrief (stale is acceptable) — fixed: `TurnLogReplayer` and
      `ConsolidateMemory.execute` (now plain `def`, no real `await` inside — see
      `services/memory.py`) are dispatched via `asyncio.to_thread` from
      `_run_offline_pipeline` in `server.py`, so a long consolidation run no longer blocks
      the event loop. `GenerateMemoryBrief` stays directly `await`ed — it genuinely
      cooperates via `ollama.AsyncClient`.
      Also fixed along the way: the offline pipeline now uses a **second, dedicated
      Postgres connection** (`ServerContext.offline_conn` + `offline_*` repos/`PSUnitOfWork`,
      wired in `main()`), separate from the live per-connection `conn`. A single shared
      connection would have let the background thread's open per-conversation transaction
      race against a live `StartSession` query on the same logical Postgres session —
      either executing as part of that transaction or blocking the event loop waiting on
      the connection, defeating the fix. Two independent connections avoid this entirely.
      **Known residual limitation (documented, not fixed)**: `TurnLogReplayer`'s
      idempotency check (`is_persisted(session_id)` then insert) is not atomic across the
      two connections, and `turns.session_id` has only a plain index, no uniqueness
      constraint. If a client reconnects in the narrow window while the background thread
      is mid-replay of that exact not-yet-committed session, both connections could decide
      "not yet persisted" and each insert a duplicate Conversation+Turn set for it. Judged
      very unlikely in practice (requires a reconnect landing in a sub-second-to-low-
      single-digit-second window right as the idle timer fires) and not a crash/corruption
      risk, just duplicate data for that one session — deferred rather than adding a claim
      table (e.g. `replayed_sessions(session_id UUID PRIMARY KEY)` with
      `INSERT ... ON CONFLICT DO NOTHING RETURNING session_id`) right now.
- [x] End-to-end test: disconnect → verify Conversations consolidated + DB state correct —
      `server/tests/integration/test_consolidation_pipeline.py` (2026-07-09), unblocked by
      Phase 3's real-Postgres fixture. Real JSONL session file → real `JSONLSessionReplayReader`
      → real `TurnLogReplayer` → real `PSConversationRepository` → real `ConsolidateMemory` with
      real `PSUnitOfWork`, Fakes only for the LLM-dependent ports (extraction, worthiness,
      disambiguation, synthesis). Verifies: replay produces the right unconsolidated
      Conversation; consolidation flips `consolidated`/`worthiness` in the DB; extracted
      Episode/Concept rows are actually queryable afterward via `PSMemoryRepository.search()`.
      Second test covers the worthy/unworthy split (Concepts always extracted, Episodes only
      when worthy) against the real DB. 2/2 passing live.

---

## Phase 6 — MemoryBrief Generation and Session Injection

The assistant has meaningful context from past conversations at every session start.

- [x] GenerateMemoryBrief service wired at end of each full consolidation run —
      `_run_offline_pipeline` in `server.py` (Phase 4 Pass 2), only runs if `processed > 0`
- [x] MemoryBrief overwritten (single record, always current) — `PSMemoryBriefRepository.save()`
      does `INSERT ... VALUES (1, ...) ON CONFLICT (id) DO UPDATE`, fixed `id=1` singleton
- [x] StartSession injects MemoryBrief content as static system-level block —
      `_compose_working_context` in `services/session.py` appends `wm.memory_brief.content`
      to the system prompt; unit-tested by `test_injects_memory_brief` (Phase 2)
- [x] End-to-end test: two sessions; second session's LLM context contains summary of first —
      `server/tests/integration/test_memory_brief_injection.py` (2026-07-09), same real-Postgres
      fixture as Phase 5's test. Real `GenerateMemoryBrief` (Fake LLM — what the LLM would say
      isn't what's under test, the plumbing that gets its answer into the next session's prompt
      is) saves a brief via real `PSMemoryBriefRepository`; real `StartSession` (real
      `PSUserRepository`/`PSPersonaRepository`/`PSMemoryBriefRepository`) pulls it back for
      "session 2"; asserts the brief content is both on `WorkingMemory.memory_brief` and
      literally present in the composed system prompt returned by the real
      `_compose_working_context`. 1/1 passing live; full suite 141/141 (up from 138, no
      regressions from either Phase 5 or Phase 6's new tests).

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

### TODO — model download/caching + CUDA compatibility (from Phase 4 Pass 2 live test, 2026-07-06)

The live smoke test on the GPU workstation (see Phase 4 Pass 2 findings above) surfaced
exactly the class of problems a proper installation wizard should prevent on a fresh box —
none of this should require hand debugging on a real install:

- **Model download/caching should be a wizard responsibility, driven by the user's primary +
  secondary language selection.** Right now, `SentenceTransformerEmbeddingService`
  (`multilingual-e5-large`) and Kokoro voice packs (e.g. `af_heart.pt`, `ff_siwis.pt`) are
  lazily downloaded on first *live* use — which fails outright on a locked-down/offline
  server (no proxy allowed on the live process, see `HF_HUB_OFFLINE=1` fix in
  `infrastructure/embedding.py`) unless someone manually pre-downloads them first. The
  wizard should download every asset actually needed for the languages selected in step 6
  (`SelectLanguages`) — the embedding model (always, single shared model) plus only the
  Kokoro/Piper voice packs for the chosen languages — during `ResolveTTSEngines`/a new step,
  not leave it to chance at first conversation.
- **`SSL_CERT_FILE`/corporate-proxy handling for one-time downloads should be automatic, not
  a manual env var the developer has to remember.** `docs/INSTALL_SERVER.md` documents the
  `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt` workaround, but nothing in the wizard
  applies it. `ModelInstaller` implementations should either detect/pass this automatically
  or the wizard should surface a clear, actionable error pointing at the doc instead of a
  cryptic `httpx` "client has been closed" traceback (see
  [[project-gpu-workstation-environment]] for why that error is misleading — the real cause
  is `CERTIFICATE_VERIFY_FAILED` on the *first* retry attempt, masked by a
  retry-logic bug that surfaces a different error on the second attempt).
- **CUDA major-version conflicts (e.g. `nvidia-cublas-cu12` vs the newer, differently-named
  `nvidia-cublas`) should be caught and resolved at install time, not discovered as a runtime
  crash.** Adding `torch` as a live dependency (for the embedding service) pulled in the
  CUDA-13-generation `nvidia-cublas` package, while `ctranslate2`/faster-whisper needs
  `libcublas.so.12` and declares no CUDA dependency of its own to provide it — so nothing in
  the resolved tree shipped it. **The `server`-side fix landed**: `nvidia-cublas-cu12` is now
  pinned explicitly in `server/pyproject.toml` (not yet verified — needs a real `uv sync` +
  STT run on the GPU workstation). What's still open is the *wizard's* half of this: per-item
  #6 above (dependency installation is squarely the installation wizard's job, distinct from
  the GA's voice-config scope — see CLAUDE.md), `CheckPrerequisites`/`RunHealthChecks` should
  still verify all CUDA-dependent packages actually resolve to compatible library versions on
  a fresh install, so this class of bug is caught before first run rather than relying on this
  one pin never drifting.

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

**Verified live (GPU workstation, 2026-07-09):** `uv sync` for the `setup` package for the
first time on this box (needed the corporate-proxy `--system-certs` workaround); full
`pytest` suite 38/38 passing on Linux. `NvidiaSmiGPUDetector.detect_vram_gb()` against the
real RTX 4090 correctly returned 23.99 GB. Full wizard flow run end-to-end via a throwaway
driver script (not committed) that kept every adapter real (GPU detector, TOML catalogues,
model installer, config writer, schema runner, health checks) and only swapped
`QuestionaryPrompter` for a scripted auto-answering stand-in — legitimate given
`WizardPrompter` is already the exact seam the architecture exposes for this
(`FakeWizardPrompter` does the same for unit tests). Confirmed:
- `SelectLLM`/`ResolveSTTEngine` fit-hint text against real 24GB VRAM — correct for every
  catalogue entry (e.g. `gemma3:27b` → "Fits, but tightly", `llama3.3` → "Does not fit —
  needs at least 52 GB free", everything else → "Fits comfortably").
- `OllamaModelInstaller.download_whisper_model("small")` performed a real network download
  (6 files via `huggingface_hub`) through the corporate proxy, scoped correctly to just that
  call (see finding below).
- `HealthCheck`'s actual *success* path, not just failure: `PostgresHealthCheck`,
  `PgvectorExtensionHealthCheck`, `OllamaHealthCheck` all returned `ok=True` against the real
  services. `ServerWebSocketHealthCheck` correctly returned failure (no `memai-server`
  process was running) — its success path still needs a real running server, out of scope
  here.
- `SetupSchema`'s real re-apply against the real dev DB (not a test DB) was safely idempotent
  — confirmed 0 rows changed (still just the GA seed persona).
- End result: a real `memai.toml` was written (`llm=aya-expanse`, `stt.model_path="small"`,
  `languages=[en, fr]`, both routed to Kokoro) — kept in place, replacing the stale
  Phase-4-era manual config (`gemma4` stand-in), per discussion with Martin.

**Real bugs/gaps found while doing this (not Pass-7 logic bugs — same "verify by actually
running it" category as Phase 4's findings):**
- **`ModelInstaller.pull_llm()` exists but no wizard step ever calls it — FIXED 2026-07-09.**
  `SelectLLM` picked an LLM `model_id` and wrote it to config, but nothing in
  `services/steps.py` invoked `installer.pull_llm(model_id)` — confirmed by reading every
  step and grepping for call sites; matches the original flow doc's step 5, "LLM selection +
  ollama pull," which was never fully implemented. Fixed: `SelectLLM.run()` now calls
  `self._installer.pull_llm(plan.llm_model_id)` right after the selection, wrapped in a
  warn-and-confirm guard matching `CheckPrerequisites`' existing pattern — on failure
  (`OSError`/`subprocess.SubprocessError`), asks via `prompter.confirm(..., default=False)`
  whether to continue anyway, raising `WizardAborted` if declined. `SelectLLM.__init__` now
  takes a `ModelInstaller` (constructor signature change; `cli.py`'s one call site updated).
  `FakeModelInstaller` gained a `fail_pull_llm` constructor flag to test the failure path.
  Two new unit tests (`test_select_llm_step.py`): pull is called with the chosen model_id;
  decline-on-failure raises `WizardAborted`; confirm-on-failure continues. 41/41 setup-package
  tests passing (Windows + Linux). **Live-verified on the GPU workstation**: a real `ollama
  pull` failure (blocked by the same shared-daemon corporate-proxy limitation noted above —
  not something this fix addresses, and already declined to touch since it's a shared systemd
  service affecting other lab users) now degrades gracefully with a clean `WizardAborted`
  message instead of a raw traceback, confirmed via direct real-subprocess test.
- **Confirmed exactly as documented, not new**: `_install_steps()` in `cli.py` builds
  `PostgresHealthCheck`/`PgvectorExtensionHealthCheck` from `InstallationPlan().database_url`
  (the class default, `...changeme@localhost...`) *before* the wizard runs — completely
  decoupled from whatever `plan.database_url` ends up being, since no step currently sets it.
  Reproduced live: the checks failed with a real password-auth error until the driver script
  substituted the real credentials directly into the check construction (bypassing
  `plan.database_url` entirely, since reading it wouldn't have helped). Confirms the "Known
  gaps" note below is accurate and still blocking, not stale.
- **Corporate-proxy env vars must be scoped to just the download call, not the whole
  process** — confirmed by reproducing the exact mistake once: wrapping the entire driver
  script in `http_proxy`/`https_proxy` (to let the Whisper download through) silently routed
  `OllamaHealthCheck`'s `localhost:11434` request through the proxy too, which returned `403
  Forbidden`. Fixed in the driver by wrapping only the `ModelInstaller` calls in a
  context-managed proxy scope. Matches `project_gpu_workstation_environment` memory's existing
  warning about this exact pitfall — good to have it concretely reproduced once rather than
  just documented in the abstract.

---

## Phase 8 — Config Placement & Persona Lifecycle Refactor

Every setting lives in its architecturally correct home (bootstrap `memai.toml` vs.
domain-owned DB attribute), and `Conversation`↔`AssistantPersona` traceability is a real
FK instead of a denormalized snapshot that can't actually deliver point-in-time fidelity.
Decisions and rationale: see memory `project_config_placement_persona_lifecycle` (2026-07-07).
This phase does **not** include wiring GA to actually change these settings by voice
mid-conversation — see "Explicitly not in this phase" below.

### Domain (`server/src/memai_server/domain/`)
- [x] `AssistantPersona`: add `speaking_rate: float = 1.0` (persona-scoped, mirrors `tts_voice` —
      a language-tutor persona will want a different rate than GA)
- [x] `AssistantPersona`: add `is_active: bool = True`
- [x] `AssistantPersona`: add `deactivate()`/`reactivate()` methods — `deactivate()` raises if
      `is_system` (GA can't be deactivated, same guard `RemovePersona` already has for deletion)
- [x] `AssistantPersona.update()`: drop the `is_system` check entirely — `name`/`system_prompt`
      become editable on any persona including GA (resolves the `is_system` guard-split decision,
      closes `project_memai_open_questions` item 12). Also extended (beyond the original scope
      here) to accept `tts_voice`/`speaking_rate`/`response_language` so `EditPersona` has one
      domain method to route every persona-settings mutation through, rather than reaching into
      attributes directly from the service layer.
- [x] `User`: add `idle_consolidation_minutes: float = 5.0`, plus an
      `update_idle_consolidation_minutes()` method mirroring `update_primary_language()`
- [x] `Conversation`: replace `persona_snapshot: AssistantPersona` field with `persona_id: UUID`
- [x] New domain events `PersonaDeactivated`/`PersonaReactivated` in `domain/events.py`, mirroring
      the existing `PersonaSwitched`/`PrimaryLanguageChanged` pattern
- [x] **Not in scope**: promoting `merge_threshold`/`disambiguate_threshold` to persona-scoped
      fields — stays deferred pending real calibration data (see `CLAUDE.md` and
      `project_config_placement_persona_lifecycle`)

### Services (`server/src/memai_server/services/`)
- [x] `CreatePersona`: accept a `speaking_rate` param (default 1.0)
- [x] `EditPersona`: extend to also accept `tts_voice`/`speaking_rate`/`response_language`,
      making it the one canonical "modify persona settings" use case. The onboarding flow
      in `server.py` (previously mutating `session.active_persona.tts_voice`/`response_language`
      directly and calling `persona_repo.save()`, bypassing use cases entirely) now routes through
      `EditPersona`
- [x] New `DeactivatePersona`/`ReactivatePersona` use cases in `services/persona.py` — additive
      alongside the existing `RemovePersona` (hard delete + cascade), which is left untouched as
      the future "purge" path
- [x] New `UpdateIdleConsolidationMinutes` use case in `services/user.py`, mirroring
      `UpdatePrimaryLanguage`
- [x] `services/replay.py`: conversation construction builds `Conversation(persona_id=persona.id,
      ...)` instead of `persona_snapshot=persona` — no longer needs the full persona object, just
      `group.persona_id` (with the existing `general_assistant` fallback)
- [x] `services/memory.py`, `infrastructure/llm/ollama.py`, `infrastructure/llm/openrouter.py`:
      change every `conversation.persona_snapshot.id` to `conversation.persona_id`
- [x] `server.py`: idle-consolidation scheduling (`_run_offline_pipeline_after_idle`) reads
      `session.user.idle_consolidation_minutes` instead of `ctx.idle_consolidation_minutes`
      (the latter field removed from `ServerContext`/`ServerConfig` entirely)

### Infrastructure (`server/src/memai_server/infrastructure/`)
- [x] `postgres.py` `PSConversationRepository`: read/write the `persona_id` column instead of
      `persona_snapshot` JSONB
- [x] `postgres.py`: remove now-dead `_persona_to_jsonb`/`_jsonb_to_persona` helpers (only ever
      used for `persona_snapshot`) — replaced with a `_row_to_persona` row-tuple helper shared by
      `get`/`list_all` now that persona rows carry two more columns
- [x] `postgres.py` `PSPersonaRepository`: read/write the new `speaking_rate`/`is_active` columns
- [x] `postgres.py` `PSUserRepository`: read/write the new `idle_consolidation_minutes` column
- [x] `infrastructure/tts.py`: `KokoroTTSService.synthesise()` takes a `speed` param instead of
      the hardcoded `speed=1.0`, sourced from `persona.speaking_rate` (threaded through the
      `TTSService` port and both call sites in `services/session.py`)
- [x] `infrastructure/config.py` / `server/config/memai.example.toml`: removed
      `idle_consolidation_minutes` from `[server]`; removed the `[voice_configurable]` section and
      `update_voice_config()` entirely (confirmed unused — no callers anywhere in the monorepo).
      `CLAUDE.md`'s "Voice-only configuration" design-constraint bullet updated to describe the
      DB-attribute placement rule instead of the now-defunct toml section, so the doc doesn't
      contradict the implemented architecture.
- [x] `setup/src/memai_setup/infrastructure/config_writer.py`: stopped writing
      `voice_configurable` (it never wrote `idle_consolidation_minutes` in the first place —
      that only ever came from `ServerConfig`'s own default, not the wizard)

### Schema (`server/migrations/001_initial_schema.sql`)
- [x] `personas`: add `speaking_rate DOUBLE PRECISION NOT NULL DEFAULT 1.0`,
      `is_active BOOLEAN NOT NULL DEFAULT TRUE`
- [x] `users`: add `idle_consolidation_minutes DOUBLE PRECISION NOT NULL DEFAULT 5.0`
- [x] `conversations`: replace `persona_snapshot JSONB NOT NULL` with
      `persona_id UUID NOT NULL REFERENCES personas(id) ON DELETE RESTRICT` — see resolved open
      question below
- [x] Update the GA seed `INSERT` with the two new persona columns
- [x] Applied against the real dev database on the GPU workstation (2026-07-08) — dropped and
      recreated the `public` schema (old data was 2 disposable test conversations), re-ran
      `001_initial_schema.sql`; confirmed all new columns present (`personas.speaking_rate`/
      `is_active`, `users.idle_consolidation_minutes`, `conversations.persona_id` FK) and the
      GA seed row correct

### Tests
- [x] Update fixtures constructing `AssistantPersona`/`Conversation`/`User` across
      `server/tests/` — `persona_snapshot`→`persona_id` touched `test_persona.py` (domain),
      `test_conversation.py`, `test_consolidation.py`, `test_replay.py`, `services/test_persona.py`;
      `services/test_session.py` needed no changes (only uses the `general_assistant()` factory,
      never constructs `AssistantPersona`/`Conversation` directly)
- [x] New unit tests: `deactivate()`/`reactivate()` behavior (including the GA-cannot-deactivate
      guard), `update()` no longer rejecting `is_system` edits (plus a new test for the
      `tts_voice`/`speaking_rate`/`response_language` update path), `UpdateIdleConsolidationMinutes`,
      `EditPersona`'s new fields, `DeactivatePersona`/`ReactivatePersona` use cases
- [x] `FakeTTSService.synthesise()` signature updated to match the new `speed` param (not
      explicitly called out above, but required by the `TTSService` port change)

**Verified (Windows dev workstation, no GPU/DB):** `uv run pytest` — 99/99 passing (full
server suite); `ruff check` on `server/src`, `setup/src`, `client/src` — the only 2 findings
(`E741` ambiguous variable name `l`, `infrastructure/postgres.py`) are pre-existing, confirmed
via `git stash` diff (3 instances before this change, 2 after — one was inside the now-deleted
`_persona_to_jsonb` helper), not introduced by Phase 8.

**Verified live (GPU workstation, 2026-07-08):** `uv sync` clean (required the corporate-proxy
`--system-certs` workaround, see `project_gpu_workstation_environment` memory); full `pytest`
suite 99/99 passing on Linux against the real venv. Real `faster_whisper.WhisperModel` loaded on
CUDA and ran a transcribe call successfully — confirms the `nvidia-cublas-cu12` pin (Phase 4
Pass 2 finding) actually resolves `libcublas.so.12` with no `LD_LIBRARY_PATH` workaround needed.

- [x] End-to-end test: real Postgres, exercise `DeactivatePersona`/`ReactivatePersona` and the
      new `persona_id` FK's `ON DELETE RESTRICT` behavior against real conversation history —
      verified live 2026-07-08 via a throwaway script (not committed) against the real recreated
      DB: (1) GA (`is_system`) deactivation correctly raises `ValueError`; (2) `RemovePersona`
      hard-deletes a persona with no conversation history; (3) `RemovePersona` on a persona with
      a real `conversations` row raises `ForeignKeyViolation` (`ON DELETE RESTRICT` confirmed);
      (4) `DeactivatePersona`/`ReactivatePersona` both succeed on that same persona instead,
      firing `PersonaDeactivated`/`PersonaReactivated` and flipping `is_active` correctly. DB
      left clean afterward (1 persona = GA seed, 0 conversations).

### Open questions to resolve during implementation (not settled by prior discussion)
- [x] `ON DELETE` behavior for the new `conversations.persona_id` FK, given `RemovePersona`'s hard
      delete is left in place — **decided: `RESTRICT`**. Matches "session logs kept forever":
      once a persona has any conversation history, `RemovePersona`'s hard delete becomes
      permanently blocked for it (an FK violation), forcing `DeactivatePersona` instead —
      `CASCADE` would silently violate the log-retention invariant, and `SET NULL` isn't legal
      against a `NOT NULL` column. In effect, hard delete is now only usable for personas that
      were created and abandoned without ever being used in a conversation; anything with real
      history must be deactivated, not deleted. This narrowing is judged correct, not a
      regression — the dual-lifecycle design's whole point was to make deactivation the normal
      path once a persona has actually been used.

**Decided 2026-07-07**: no migration framework needed — still in dev, no data worth preserving.
Just edit `001_initial_schema.sql` in place with the new column definitions (no `ALTER TABLE`
statements) and drop/recreate the local dev DB before re-running it.

### Explicitly not in this phase
- Live voice-command wiring — LLM tool-calling / intent detection to actually *trigger*
  `UpdateIdleConsolidationMinutes`, `DeactivatePersona`, or a voice/speaking-rate change
  mid-conversation. This phase only gets the data model and use cases into a correct, consistent
  state; wiring GA to invoke them by voice is separate, larger, undesigned work (candidate Phase 9)
- VAD silence-frame threshold voice-configurability — needs a new server→client WS message,
  unrelated to this DB/config refactor
- Merge/disambiguate threshold promotion — blocked on real calibration data

---

## Phase 9 — Live Voice-Command Wiring (not yet designed)

Not started; not yet grilled/scoped the way Phases 1-8 were. Captured here as a placeholder so
the next design session has a starting point, per the discussion that closed out Phase 8
(2026-07-07). Scope, ordering, and the items below are all open — nothing in this section is a
committed decision.

### Known scope (from Phase 8's "explicitly not in this phase")
- [ ] LLM tool-calling / intent detection so GA can actually *trigger*, mid-conversation:
      `UpdateIdleConsolidationMinutes`, `DeactivatePersona`/`ReactivatePersona`, and a
      voice/speaking-rate or `tts_voice` change via `EditPersona`. Phase 8 only built the data
      model and use cases these would call — none are wired to live conversation yet.

### Prerequisite design question (surfaced 2026-07-07, see `project_memai_open_questions` item 15)
- [ ] **Discovery/registry for "what's voice-configurable"** — Phase 8 answered *where* each
      setting's value lives (a DB attribute of `User` or `AssistantPersona`), but not *how GA
      knows an attribute exists, its valid type/range, or which use case to invoke to change it*.
      Today GA's only "knowledge" of what it can configure is hand-written prose in
      `ONBOARDING_SCRIPT` (`services/session.py`) — human-maintained, not bound to the actual
      entity fields, already stale (`idle_consolidation_minutes`/`speaking_rate` exist as real
      fields but aren't mentioned there). A candidate direction floated in passing (not decided):
      a small declarative registry — e.g. a `VoiceConfigurableField` descriptor per attribute
      (entity, field name, type, validator, use case to call) — that both `ONBOARDING_SCRIPT` and
      the tool-calling layer above could read from, instead of two hand-maintained lists drifting
      independently. This is likely a prerequisite for the tool-calling item above, not a parallel
      track — needs its own design session before implementation starts.

### Other known candidates, not yet scoped
- [ ] `StartSession`'s two hardcoded, unwired constructor defaults (`session_tail_turns: int = 10`,
      `session_continuation_threshold_hours: float = 24.0` in `services/session.py` — never passed
      a value at the `server.py` call site). Same category as `idle_consolidation_minutes`;
      candidate: promote to `User` fields under the same DB-attribute placement rule. See
      `project_memai_open_questions` item 14.
- [ ] VAD silence-frame threshold voice-configurability (client-side; needs a new server→client
      WS message, since the client is documented as fully stateless)
- [ ] Merge/disambiguate threshold promotion to persona-scoped, voice-configurable fields —
      blocked on real calibration data (see Phase 3's integration test)

---

## Phase 10 — Persona Extension Foundations (schema + port contracts)

Design settled 2026-07-10 (MEO BR-doc session) — full rationale in the
`project_persona_extension_ports` and `project_language_tutor_model` memory files. This
phase lands every schema/contract change that the bundle file format (Phase 11) and the
tutor persona (Phase 12) will reference, so those are designed against a fixed target.
Deliberately excludes all tutor runtime machinery (strategies, half-life function,
two-teacher TTS streaming) — that needs bundle content and calibration data to be
meaningful and belongs in Phase 12.

### Schema + domain (one migration)
- [x] `category: str | None` on `Concept`/`Procedure` — free-text, persona-interpreted
      (taxonomies live in the persona's own vocabulary, e.g. tutor's noun/verb/idiom/
      contrast_pair, morphological_pattern/construction/rules); domain fields + migration
      (`001_initial_schema.sql` edited in place per the Phase 8 no-migration-framework
      decision) + `postgres.py` round-trip (INSERT and UPDATE both carry it) + extraction
      plumbing (`_parse_extraction` reads an optional `"category"` key; the shared
      extraction prompt asks for a "short lowercase classification label or null").
      **Merge rule decided during implementation**: on upsert-merge, the existing
      category wins and the new one only fills a gap (`existing.category or new.category`
      in `ConsolidateMemory`) — curated bundle content must not be overwritten by a
      generic extractor's guess. Unit-tested both ways.
- [x] `persona_state: dict | None` (nullable JSONB) on `Concept`/`Procedure` — opaque,
      UNKEYED slot (persona_id FK already scopes ownership — persona-keyed map explicitly
      rejected). Single-writer contract enforced **structurally, not just by convention**:
      the upsert UPDATE statements deliberately exclude the column (a merge upsert can
      never clobber assessment state), and the only write path is the new
      `MemoryRepository.update_persona_state(memory_type, item_id, persona_state)` port
      method (raises on EPISODE — episodes have no persona_state; the
      `persona_episode_state` association table stays deferred). `search()` reads it back
      so selection strategies can rank on it.
- [x] `AssistantPersona.tts_voice: str` → `voices: dict[str, str]` (speaker role → Kokoro
      voice) — new `DEFAULT_VOICE_ROLE = "default"` constant + `default_voice` property;
      `__post_init__`/`update()` guard that the map always contains the default role.
      GA seed and `general_assistant()` factory produce a single-entry map; the live path
      (`ProcessTurn`) keeps using only the default role — per-segment speaker switching
      (two-teacher cast) is Phase 12, not here. `CreatePersona`/`EditPersona` take a
      `voices` map now; the onboarding handler in `server.py` writes
      `{"default": <derived voice>}`. Migration: `voices JSONB NOT NULL DEFAULT
      '{"default": "af_heart"}'` replaces the `tts_voice` column; seed updated;
      `CLAUDE.md`'s config-placement example updated to match.
- [x] Extractor rule: Episode summaries always written in `User.primary_language`
      regardless of conversation language — implemented as a shared
      `_extraction_system_prompt(conversation, primary_language)` in
      `infrastructure/llm/_common.py` (Ollama and OpenRouter extractors previously
      duplicated the whole prompt inline; now they can't drift), instruction emitted only
      when `primary_language` is known (pre-onboarding conversations fall back to old
      behaviour). `ConsolidationExtractor.extract()` gained a `primary_language` param;
      `ConsolidateMemory` reads it once per run via a new required `user_repo` (wired in
      `server.py` as a new `offline_user_repo` on the offline connection). Unit-tested at
      both levels: prompt text (`tests/unit/infrastructure/test_extraction_prompt.py`)
      and `ConsolidateMemory` → extractor pass-through.

### Port contracts + Fakes (no real strategies yet)
- [x] `SelectedItem(item: MemoryItem, context: str | None)` — frozen dataclass in
      `services/ports.py`; `context` injected verbatim, never interpreted
- [x] `PersonaSelectionPort.select_items(persona_id, category=None, engagement_level=None,
      limit=10) -> Sequence[SelectedItem]` — live hook, fetched once at session start
- [x] `PersonaEnrichmentPort.propose_items(persona_id) -> Sequence[MemoryItemDraft]` —
      offline hook, OPTIONAL per persona. Port + Fake only in this phase (deliberately no
      pipeline wiring — the first real consumer is the tutor's interest-cluster strategy,
      Phase 12). New `MemoryItemDraft` type alias = `Concept | Procedure` with `id=None`
      (same shape Phase 11's `InstallPersonaBundle` will emit).
- [x] `PersonaAssessmentPort.assess_items(persona_id, conversation, touched_items) ->
      Sequence[ItemAssessment]` — offline hook, OPTIONAL per persona.
      **`ItemAssessment` gained a `memory_type` field beyond the designed
      `(item_id, persona_state)` pair** — concepts and procedures have independent id
      sequences (separate SERIAL columns), so a bare `item_id` cannot identify the target
      table; the persistence call needs the discriminator.
- [x] `Fake*` implementations for all three ports (`FakePersonaSelectionPort`,
      `FakePersonaEnrichmentPort`, `FakePersonaAssessmentPort`, all call-recording) +
      `FakeMemoryRepository.update_persona_state` + unit tests
- [x] Consolidation pipeline hook: after upsert (so new items have IDs), dispatch
      `assess_items` for the conversation's persona if a strategy is registered; persist
      the returned dicts byte-for-byte via `update_persona_state`. Runs **inside** the
      per-conversation `UnitOfWork` (assessment is part of that conversation's atomic
      consolidation). Registration mechanism: a plain `dict[UUID, PersonaAssessmentPort]`
      constructor param on `ConsolidateMemory` (default empty — GA registers nothing);
      a fancier registry abstraction was deliberately not built with zero real strategies
      existing. `touched_items` = the conversation's upserted concepts + procedures;
      episodes excluded (no persona_state slot). Unit tests: dispatched-with-ids,
      persisted-verbatim, no-strategy no-op, nothing-touched no-op.
- [x] Live consumption wiring: `StartSession` takes the same style of
      `dict[UUID, PersonaSelectionPort]` registry, fetches the batch alongside
      User/MemoryBrief/Persona into new `WorkingMemory.selection_batch` (skipped during
      onboarding, like MemoryBrief); `ProcessTurn` consumes **one item per turn** (default
      generic pacing until Phase 12's strategy-driven policy exists), injecting
      `item + context` as a role-tagged system message placed just before the current
      user turn — same mechanism as RAG recall, per the settled design. GA registers no
      strategy → empty batch → no-op path. Unit tests: batch fetched at start, skipped in
      onboarding, injected content + context present in LLM messages, one-per-turn
      consumption until exhausted, no injection without a batch.

### Verification (2026-07-10, Windows dev laptop — no GPU/DB)
- 103/103 unit tests passing (up from 99; server suite), `ruff check` clean on all three
  packages' `src` (only the 2 pre-existing `E741`s in `postgres.py`, same as Phase 8),
  full `compileall` syntax pass on `server/src`.
- Integration tests updated (`test_postgres.py` persona fixture → `voices`;
  `test_consolidation_pipeline.py` → new `user_repo` param) **and extended with new
  Phase 10 round-trip tests**: multi-entry `voices` map round-trip,
  concept/procedure `category` + `persona_state` round-trips including
  upsert-doesn't-clobber-persona_state, and `update_persona_state` EPISODE rejection.
  Initially not runnable on this laptop (venv has no `psycopg` — integration runs have
  always lived on the GPU workstation); done for real the same day, see below.
- [x] GPU workstation run (2026-07-10, tx940094): **165 passed, 5 skipped** — full suite
      including every new Phase 10 integration test against real Postgres (the 5 skips are
      the known missing voice packs es/it/pt/ja/zh-cn, the standing Phase 7 TODO).
      Dev DB re-created from the edited `001_initial_schema.sql` after confirming it held
      only the GA seed row (0 users/conversations/memories): `personas.voices` JSONB with
      correct `{"default": "af_heart"}` seed, `category`/`persona_state` present on
      concepts and procedures, and the migration confirmed still idempotent on a second
      apply. Real `memai-server` boot against the new schema verified end-to-end wiring
      (`_ensure_user_exists` on new schema, new `offline_user_repo`, model loads,
      "Server listening on :8765"), then shut down via `fuser -k 8765/tcp`.
      Getting the code there: `git pull` was blocked by a dirty workstation tree — local
      edits + untracked integration-test files from the 2026-07-08/09 live sessions that
      the pushed commits now track. Verified file-by-file (CR-insensitive) that every
      local difference was already contained in the pushed commits (the only content
      delta, `domain/model.py`, was just the pre-Phase-10 `tts_voice` version), then
      `git stash -u` (kept recoverable, not discarded) + fast-forward to `a631d38`.
      `uv sync --system-certs` behind the proxy was a clean no-op (Phase 10 adds no deps).
      Note: `--native-tls` is now a deprecated alias — `--system-certs` (as recorded in
      the workstation memory) is the current flag name.

### Findings / side-fixes from this phase
- **`infrastructure/llm/__init__.py` eagerly imported the OpenRouter family, making
  `openai` a hard import-time requirement even for fully-local Ollama deployments** —
  against the project's fully-local default (the OpenRouter family is explicitly the
  opt-in cloud alternative). Fixed: OpenRouter names are now lazily re-exported via
  module `__getattr__`; importing the package or the Ollama family no longer touches
  `openai`. (Surfaced because this laptop's venv predates the `openai` dependency.)
- **This laptop's venv cannot be re-synced: `uv.lock` pins `numpy==1.26.4`** (locked on
  the Linux GPU box), which has no cp313 wheels and fails to build from source on
  Windows/Python 3.13 — while the venv actually contains numpy 2.4.4 from an earlier
  resolution. Any `uv sync` here dies on the numpy build (after first dying on the
  corporate proxy without `--native-tls`). Not a Phase 10 issue and not fixed here —
  flagging that the lock needs a re-resolve (`uv lock --upgrade-package numpy`) next time
  the GPU box is touched, or the laptop stays frozen on `uv run --no-sync`.

---

## Phase 11 — Persona Bundle Format + `InstallPersonaBundle`

Design settled 2026-07-10 — full rationale and format spec in
`docs/BRIEF_phase11_bundle_format.md`. Headline decision: **the bundle file format IS
the port** between memai and any (external, future, possibly commercial) authoring tool
— memai owns the versioned envelope schema; authors own all content vocabulary. The
authoring tool itself, the standalone validator CLI, and the content quality/safety
review pass are all explicitly out of Phase 11 scope (see brief's Non-goals).

- [x] Schema + domain: `personas.persona_key TEXT NULL UNIQUE` (author-namespaced
      identity, e.g. `meo/spanish-tutor`, convention-enforced — no registry),
      `personas.settings JSONB NULL` (opaque persona-owned tunables, e.g. the
      learner-language-keyed `pair_difficulty` map; same leak-prevention contract as
      `persona_state`), new `bundle_installs` append-only provenance log
      (2026-07-11) — `001_initial_schema.sql` edited in place; `AssistantPersona` gained
      `persona_key`/`settings` (both default `None`; GA seed untouched);
      `PersonaRepository.get_by_key()` added to port + `PSPersonaRepository` + Fake.
      **`persona_key` is excluded from the save() UPDATE branch** (like `is_system`):
      identity set at creation by the installer, structurally never reassigned.
      `bundle_installs.persona_key` is plain TEXT, no FK — the provenance log must
      survive persona deletion. Unit tests green (105 passed on laptop; the separate
      `test_config.py` collection error is just this laptop's frozen venv missing
      `platformdirs`, a declared dep — runs on the workstation). New integration tests
      (round-trip, `get_by_key`, unique violation, key-not-reassigned) written but
      pending the workstation run + schema re-apply, tracked with the step-6 item.
- [x] Refactor: extract the merge-or-insert upsert machinery
      (`_merge_action`/`_existing_to_merge` + embedding + synthesis) from
      `ConsolidateMemory` into a shared upserter used by both consolidation and the
      installer — pure move, no behavior change (2026-07-11) — new
      `services/upsert.py`: `MemoryUpserter.upsert_episode/upsert_concept/upsert_procedure`
      (mutate item in place, return True on merge — the merged-flag return is the one
      API addition, for the installer's inserted/merged provenance counts; consolidation
      ignores it). Thresholds/`_MergeAction`/`_existing_to_merge`/`_max_engagement`
      moved there; `ConsolidateMemory`'s constructor signature is unchanged (it builds
      the upserter internally from its already-injected ports), so no construction
      site — server.py or tests — changed. 105 unit tests green, ruff clean (same 2
      pre-existing E741s only).
- [x] `PersonaBundleSource` port + TOML reader adapter — bundle = directory
      (`bundle.toml` manifest + `lessons/*.toml`, ~15–40 `[[items]]` per lesson file);
      stdlib `tomllib`, read-only; `format_version = 1` from day one
      (2026-07-11) — port + parsed-form value objects in `services/ports.py`
      (`PersonaBundle`, `BundleLesson`, `BundleItemSpec`, `BundlePersonaDefinition`,
      `BundleFormatError`, `BUNDLE_FORMAT_VERSION`); adapter
      `infrastructure/bundle_toml.py` (`TomlPersonaBundleSource`). Items are format-level
      specs, NOT domain entities — persona_id doesn't exist at parse time (the installer
      resolves/creates the persona), engagement_level/embedding are installer-owned.
      Parse-and-reject enforces the spec's negative rules via an item-key allowlist:
      a bundle shipping `engagement_level`/`persona_state`/`embedding` is rejected
      loudly, as are steps-on-a-concept, unknown item types, empty lessons, and missing
      required fields. `[persona]` may omit `voices["default"]` (installer derives it).
      Manifest `[bundle]`+`[provenance]` kept verbatim for the provenance log, with TOML
      dates coerced to ISO strings (JSONB-safe). `FakePersonaBundleSource` added.
      21 new unit tests (happy paths incl. filename-sort ordering + 15 rejection cases);
      126 unit tests green on laptop, ruff clean.
- [x] `InstallPersonaBundle` use case — one-shot, session loop never calls it; persona
      exists (by `persona_key`) → attach content, absent → create from `[persona]`
      (upgrade/overwrite semantics deferred); per-lesson `UnitOfWork`; recovery =
      re-run (idempotent by merge; exact-duplicate short-circuit optimization); items
      always inserted `UNSEEN`, no `persona_state`, embedding computed at install;
      **insertion order is the contract** (lesson filename sort → SERIAL id = curriculum
      order; Phase 12 selection tiebreaks UNSEEN by ascending id); pair-independence
      rules: `voices["default"]` derived from `User.primary_language` when omitted,
      pair-specific content ships in per-pair accelerator bundles
      (2026-07-11) — `services/bundle_install.py`: `InstallPersonaBundle`,
      `BundleInstallResult` (persona_id, persona_created, counts, notices),
      `BundleInstallError` (well-formed bundle, install can't proceed — distinct from
      `BundleFormatError`). Voice derivation comes in as a `default_voice_for:
      Callable[[Language], str]` constructor param (composition root wires the same
      `KOKORO_DEFAULT_VOICES` lookup onboarding uses — the use case can't import
      infrastructure). **Open question resolved — `languages` union semantics**: bundle's
      target list in bundle order, `User.primary_language` appended iff not already
      present. Persona creation requires an onboarded user (primary_language set) —
      clear error otherwise; the attach path needs no user at all. Existing-persona +
      `[persona]` in bundle → definition ignored with a notice in the result (upgrade
      deferred). Exact-duplicate short-circuit implemented in `MemoryUpserter` (same
      name+description — steps included for procedures — skips LLM synthesis and
      re-embed; max-engagement/category rules still apply, so a reinstall can't
      downgrade knowledge). Provenance via new `BundleInstallLog` port +
      `BundleInstallRecord` VO (ports.py), `PSBundleInstallLog` (postgres.py, append-only,
      no read methods by design), `FakeBundleInstallLog`; `FakeUnitOfWork` now counts
      enter/exit so tests assert per-lesson granularity; `FakeMemorySynthesizer` now
      records calls. 21 new unit tests (6 upserter contract incl. short-circuit,
      15 installer) → 147 green on laptop; `PSBundleInstallLog` integration test +
      `bundle_installs` added to conftest truncate list, queued for the workstation run.
- [x] `memai-bundle install <path>` console script on the server package (needs
      embedding model + DB + config); documented run-while-idle caveat
      (2026-07-11) — `bundle_cli.py` + `[project.scripts]` entry written and ruff-clean;
      thin composition root mirroring `server.py`'s `main()` (truststore inject,
      `load_config`, `postgres.connect`, `SentenceTransformerEmbeddingService`,
      Ollama disambiguator/synthesizer, `KOKORO_DEFAULT_VOICES`-based
      `default_voice_for` — same derivation as onboarding). Exit 1 with a clean message
      on `BundleFormatError`/`BundleInstallError`. Live-verified on the GPU workstation,
      see below.
- [x] Hand-written mini-bundle fixture + unit tests + integration test (real Postgres,
      GPU workstation)
      (2026-07-11, laptop half done, workstation half same day) — committed fixture
      `server/tests/integration/fixtures/spanish_mini/` (persona_key
      `memai-test/spanish-mini`, `[persona]` with voices omitting "default" to exercise
      derivation, 2 lessons / 5 items, es content) — parse verified live against the real
      `TomlPersonaBundleSource` on the laptop, including the TOML-date→ISO coercion.
      `tests/integration/test_bundle_install.py`: real TOML reader + real repos/UoW/
      pgvector, Fakes only for LLM ports, and a **hash-seeded deterministic
      `HashEmbeddingService`** (distinct texts → near-orthogonal vectors → insert;
      identical text → identical vector → similarity 1.0 → exact-duplicate merge) — a
      constant-vector fake would falsely auto-merge distinct items through real pgvector.
      Covers: fresh install (persona created, voices/languages/settings, curriculum order
      as ascending SERIAL ids, `unseen` in DB, real search round-trip) and reinstall
      (0 inserted / 5 merged, zero synthesis calls, persona untouched + notice, two
      append-only `bundle_installs` rows).

      **Workstation run (2026-07-11)** — deviated from the plan below in a few places,
      recorded for next time:
      1. `git status`/`git pull`: tree was already clean and up to date with
         `origin/master` at `d19b9d0` — no pull needed.
      2. `uv lock --upgrade-package numpy` turned out moot here: `uv sync` had already
         been run on this box before this session (numpy 1.26.4 matches the lock,
         `memai-bundle`/`memai-server` both already registered in `.venv/bin`) — the
         laptop's frozen-lock problem is Windows/cp313-specific and doesn't reproduce
         on this Linux box.
      3. Skipped — already synced (see above).
      4. **Dropped and recreated the whole `public` schema instead of `ALTER TABLE`**
         (explicitly OK'd — dev DB held only 1 trivial bootstrap user, 0 conversations).
         Two things this surfaced that the plan didn't anticipate: (a) the `memai` role
         isn't superuser, so `CREATE EXTENSION vector` fails on a fresh schema — fixed by
         marking pgvector `trusted = true` in `vector.control` (one-time, standard fix for
         non-superuser app roles; `CREATE EXTENSION` as superuser was still needed once
         for the already-partially-applied dev DB before the control-file fix took
         effect); (b) since a full schema drop wipes the bootstrap user too, had to
         re-insert one with `primary_language` set (via `PSUserRepository`, matching
         `_ensure_user_exists`'s shape) before the fresh-install smoke test, since persona
         creation from a bundle requires an onboarded user.
      5. Full suite: **150 unit tests green** (up from 147 — includes `test_config.py`,
         fine on this box) and **67 integration tests green, 7 skipped** (5 known
         voice-pack gaps + 2 no-CUDA STT skips — this box's CUDA driver doesn't match the
         installed runtime; unrelated to Phase 11). Integration DB (`memai_test`) needed
         the same pgvector-trusted fix, plus a password set on the `memai` role
         (`ALTER ROLE memai WITH PASSWORD 'memai'`) since `conftest.py`'s hardcoded
         default test DSN connects over TCP (scram-sha-256), not the peer-auth socket the
         app itself uses. **Found and fixed one real, pre-existing test bug** (not a
         Phase 11 regression): `test_persona_delete_restricted_once_referenced_by_a_conversation`
         asserted `psycopg.errors.ForeignKeyViolation`, but Postgres raises the distinct
         `RestrictViolation` (SQLSTATE 23001) for `ON DELETE RESTRICT` specifically —
         confirmed against real Postgres 18, not version-specific behavior. Fixed the
         assertion in `test_postgres.py`.
      6. Live smoke, exactly as planned: fresh install → persona created,
         5 inserted / 0 merged; re-run → 0 inserted / 5 merged, ~instant (dominated by
         embedding-model load, no LLM synthesis calls). Verified in DB directly: 2
         append-only `bundle_installs` rows, 5 `unseen` items (3 concepts + 2 procedures)
         in curriculum order. Ambient `HTTP_PROXY`/`HTTPS_PROXY` were set in the shell but
         didn't need clearing — no network calls hit them (embedding model loaded from
         local cache). Cleanup: deleted the `memai-test/spanish-mini` persona — cascade
         removed its concepts/procedures, the 2 `bundle_installs` rows survived as
         designed.
      7. This update.
- [x] Authoring guide doc (replaces the former "multi-pass LLM authoring strategy" code
      item): roster workflow, no-two-unknowns, ephemeral-generation, MEO-BR
      lesson-ordering template — doubles as the seed requirements doc for the future
      authoring app
      (2026-07-11) — `docs/AUTHORING_BUNDLES.md`: format ground rules (installer-enforced
      negatives, ~300-word cap, insertion-order contract, persona_key namespacing),
      pair-independence + accelerator guidance, the settled category taxonomy tables,
      the 4-pass roster workflow (roster → no-two-unknowns ordering validation →
      descriptions → review + provenance stamping), ephemeral-generation, the MEO-BR
      lesson-ordering template (with the Zipf token-coverage caveat), and the
      requirements-seed checklist for the future authoring app (no-two-unknowns
      validator flagged as the highest-value automation; format-is-the-only-coupling
      restated; knowledge-profile export as the per-user path).

---

## Phase 12 — Language Tutor Persona (first concrete extension)

All tutor runtime machinery, buildable once Phase 11 provides content. Full design in
`docs/BRIEF_phase12_tutor.md` (consolidated from the language-tutor design record,
2026-06-29 → 2026-07-10; wiring decisions below settled 2026-07-12).

- [x] Wiring foundation (2026-07-12) — three gaps closed before any tutor code:
      1. **Strategy binding**: new nullable `AssistantPersona.strategy` column/field
         (e.g. `"language_tutor"`), set from the bundle's optional `[persona] strategy`
         key (additive, format_version still 1) and passed through by
         `InstallPersonaBundle`; creation-time identity like `persona_key` (absent from
         the repo's UPDATE branch). Composition-root registry in `server.py`
         (`SELECTION_STRATEGY_FACTORIES`, empty until the tutor strategy lands) resolves
         names per connection via `_build_selection_strategies`; unknown names warn and
         bind nothing.
      2. **Lazy, focus-aware selection batches** (the Phase 10 eager fetch would never
         have fired for a tutor: sessions always start on GA, tutors arrive via
         mid-session `[PERSONA:]` switch). `select_items(persona_id, focus, limit)` —
         the Phase 10 `category`/`engagement_level` params dropped (generic callers had
         no basis to fill them); `focus` is the user's session wish carried VERBATIM
         ("just review old vocabulary today"), interpreted only by the strategy;
         `focus=None` = default curriculum. `ProcessTurn` owns the batches now
         (`WorkingMemory.selection_batches: dict[persona_id, list]`, key presence =
         fetched, exhausted batch never re-queried): fetch-if-missing at turn start for
         the active persona; new `[FOCUS: ...]` response marker (generic prefix parser,
         between `[PERSONA:]` and boundary markers) re-fetches and REPLACES the active
         persona's batch after switch resolution, so `[PERSONA:X][FOCUS: ...]` applies
         to X. `StartSession` no longer touches selection. Re-fetching is legal live
         (DB read, same standing as RAG recall) and doesn't violate fetch-once: a
         focused query is a different question, not the same one re-asked.
      3. **`MemoryRepository.list_items(persona_id, memory_types, category,
         engagement_levels, limit)`** — non-similarity listing ordered by ascending id
         within each type (curriculum-order/UNSEEN-tiebreak contract), concepts and
         procedures only (episodes raise ValueError). PG impl + Fake.
      150 unit tests green (laptop, `uv run --no-sync`). Docs updated: CLAUDE.md ports
      paragraph, Phase 11 brief + AUTHORING_BUNDLES.md (`strategy` key),
      BRIEF_phase12_tutor.md wiring section.
      **Workstation catch-up**: `ALTER TABLE personas ADD COLUMN strategy TEXT;`
      (laptop venv note: `tests/unit/infrastructure/test_config.py` currently fails
      collection on the frozen laptop venv — `platformdirs` missing; pre-existing,
      run with `--ignore`, verify on workstation.)
- [x] Tutor selection strategy — due-ness ranking derived from `persona_state`
      (exponential decay from `last_practiced_at` with `half_life_days`; mastery/next-due
      derived at selection time, never stored); interleaved by `category` (anti-blocking);
      Episode pairing via existing similarity search at session start; elicitation hint in
      `SelectedItem.context` on similarity miss, capped at 1–2 per batch
      (2026-07-12) — new `infrastructure/language_tutor/` package (all tutor vocabulary
      lives here; generic code never imports it): `selection.py`
      (`LanguageTutorSelectionStrategy`, registered as `"language_tutor"` in server.py's
      registry — one entry serves every language-tutor persona, any target language) +
      `focus_ollama.py` (`OllamaFocusInterpreter`: live LLM call mapping the verbatim
      focus text to `TutorFocus(mode: review|new|mixed, category, topic)` — recall-
      detector precedent; runs post-stream, not on the hot path; fails open to mixed).
      Batch composition: review pool ranked least-known-then-stalest by default —
      retention ranking (`2^(-days/half_life)`, no-state = most due) is CODED but gated
      behind `settings["ranking"]="retention"` per the instrument-now-calibrate-later
      posture; new pool in cross-type curriculum order (`created_at` then id — two
      SERIAL sequences, so raw id doesn't order across concepts/procedures); mixed
      default = `batch_review_share` (0.5) with mutual backfill; focus can restrict
      mode/category (unmatched category falls back rather than zeroing the session) or
      rank by topic-embedding similarity. Category round-robin interleave. Episode
      pairing per batch item via `search(item.embedding, EPISODE, top_n=1)`:
      anchor context at similarity ≥ `episode_anchor_threshold` (0.6 placeholder),
      else elicitation hint capped by `elicitation_cap` (default 2). New tutor settings
      keys (all opaque, in `AssistantPersona.settings`): `ranking`,
      `batch_review_share`, `episode_anchor_threshold`, `elicitation_cap`.
      20 unit tests (`test_tutor_selection.py`); 170 total green.
- [x] Tutor assessment strategy — retrievals (successful only) / errors / response
      latency (from `Turn.timestamp` deltas, weighted low) / `user_initiated` salience;
      day-granularity `last_practiced_at` (sleep-gated spacing); half-life update rule
      (2026-07-12) — `language_tutor/assessment.py` + `judge_ollama.py` + shared
      `state.py` (persona_state field names, read by selection / written here).
      `OllamaPracticeJudge`: one offline LLM call per conversation judging each touched
      item (retrievals = successful PRODUCTION only, errors, user_initiated); fails
      open to exposure-only (day anchor + session count move, counts don't). Current
      stored state is read back via `list_items` — touched extraction items always
      carry `persona_state=None` (upserts structurally exclude the column). Half-life
      rule: initial = `initial_half_life_days` (1.0) × `user_initiated_boost` (2.0 when
      user-initiated) ÷ pair difficulty (resolved from `settings["pair_difficulty"]`
      against `User.primary_language`, "*" fallback); on a NEW day (sleep gate),
      errors × `half_life_shrink` (0.5, floor 0.5d) win over successes, else
      retrievals × `half_life_growth` (2.0); same-day repetition updates counts only.
      Latency = mean assistant→user turn-timestamp delta per conversation, folded into
      a sessions-weighted running average (stored for calibration; selection doesn't
      rank on it yet). Wired: `ASSESSMENT_STRATEGY_FACTORIES` in server.py (offline
      repos + shared `_build_strategies` resolver), passed to `ConsolidateMemory`.
      11 unit tests (`test_tutor_assessment.py`); 181 total green.
- [x] Tutor enrichment strategy (`propose_items`) — interest-cluster proposals once
      consolidation shows several user-initiated Concepts sharing a theme
      (2026-07-12) — two halves. Generic: new `EnrichMemory` use case
      (`services/memory.py`) closes the Phase 10 "port + Fake only" gap — dispatches
      each persona's optional enrichment strategy AFTER consolidation (fresh
      persona_state visible), forces drafts UNSEEN (a proposal cannot claim knowledge;
      max-engagement keeps earned levels on merge), feeds them through the shared
      `MemoryUpserter` (dedup = the same merge path as bundles/extraction), one
      transaction per persona batch; wired into `_run_offline_pipeline` between
      consolidate and brief. Tutor: `language_tutor/enrichment.py` +
      `cluster_ollama.py` — seeds = user-initiated Concepts (assessment's salience
      flag) with embeddings; greedy cosine clustering
      (`interest_cluster_threshold` 0.55); a cluster of ≥ `interest_cluster_min_size`
      (3) triggers ONE proposal per run (largest cluster — enrichment stays a trickle
      beside the bundle backbone); `OllamaClusterProposer` proposes ≤
      `enrichment_batch_size` (5) surrounding vocabulary items in the cluster's
      majority language; prompt excludes only seed names — full exclusion is the
      upsert-merge dedup, per the port's strategy-internal-exclusions contract.
      11 unit tests (`test_tutor_enrichment.py` + `test_enrichment.py`); 192 total green.
- [x] Two-teacher cast — speaker-tagged LLM output parsing + per-segment Kokoro voice
      switching in the streaming path; target-teacher voice rotates across sessions
      (HVPT), native-teacher voice fixed; ONE persona, ONE LLM call (two-agent design
      rejected on latency)
      (2026-07-12) — inline `[SPEAKER:role]` tags anywhere in the response (not just
      the prefix): new `_SpeakerTagParser` in `services/session.py` incrementally
      splits the post-prefix token stream into text chunks and role switches, holding
      back partial tags; a switch flushes the open segment with the outgoing voice (a
      natural break even mid-sentence); non-tag `[` text and dangling partial tags
      pass through as spoken content; tags never reach the transcript. Roles resolve
      via `_session_voice`: unknown role → default anchor; **HVPT rotation is a
      generic voices-map semantic, not a settings key** (settings opacity would be
      violated by generic code reading `target_voice_pool` — that sketch is
      superseded): a non-default role value may be a `"|"`-separated pool, resolved
      deterministically from `session_id` (stable within a session, varies across —
      stateless, live path never writes). New entity invariant: the `"default"` voice
      must be a single voice (validated in `AssistantPersona` + bundle parser).
      10 unit tests (cast + domain guards); 202 total green.
- [x] Tutor persona prompt pack — production-before-correction as elicit-self-repair-
      then-recast; pretesting/cognate guessing; TPRS-style narrative co-construction;
      episode-elicitation behaviour with ramp-up (A0 elicitation = seeding, not practice)
      (2026-07-12) — authored as the `[persona] system_prompt` of the first real bundle
      (below): two-teacher cast rules ([SPEAKER:] tagging, target teacher
      Italian-only), [FOCUS:] emission + spoken acknowledgment of the fetch delay,
      elicit-self-repair-then-recast, pretesting/cognate guessing, TPRS story recap,
      elicitation ramp (A0 = seeding/interest detection), self-assessment gating
      (assistant-initiated weighed vs evidence; user-volunteered accepted), short
      turns for no-barge-in, no-markdown/numbers-as-words. **Live-test watch item**:
      generic `_compose_working_context` appends "Always respond in '<response_language>',
      never switch" — the prompt pack claims precedence for the cast's bilingual rules,
      but verify on the workstation that the LLM respects it; if not, the generic
      response-language line may need a persona-level opt-out (design discussion).
- [x] Half-life function calibration — assessment strategy writes `persona_state` from
      day one; selection keeps ranking by `engagement_level` until real data justifies
      switching to retention ranking (same posture as the 0.93/0.75 upsert thresholds)
      (2026-07-12) — this posture is implemented in the two strategies (assessment
      always writes; retention ranking coded behind `settings["ranking"]="retention"`,
      default engagement). The actual calibration on real usage data remains open by
      definition — revisit once sessions accumulate.
- [x] First real bundle authored via the Phase 11 pipeline
      (2026-07-12) — **`bundles/italian-a0-starter/`** (target language: Italian —
      Martin's pick; the French↔Italian cognate accelerator is a natural follow-up
      bundle, not authored yet): persona_key `memai/italian-tutor`,
      `strategy = "language_tutor"`, target-teacher HVPT pool `"if_sara|im_nicola"`,
      default anchor derived at install, `pair_difficulty` keyed by learner language
      (fr/es/pt 0.8, en 1.0, * 1.3). 4 lessons / 46 items per the authoring guide:
      01 saluti (greetings + mi chiamo/come ti chiami), 02 io-tu-essere (irregular
      essere form-by-form as atomic verbs + negation `rules` + sono-di construction),
      03 verbi frequenti (-are `morphological_pattern` with steps, vorrei
      construction, tu-vs-Lei `contrast_pair`, survival idioms), 04 al caffè (gendered
      nouns, numbers, il/la `rules`, per-me construction). No-two-unknowns ordering
      respected; descriptions in Italian (pair-independent). Parses through
      `TomlPersonaBundleSource` (verified). **Install + live smoke = workstation.**
- [ ] Workstation run (Phase 12 verification):
      1. `git pull`; `ALTER TABLE personas ADD COLUMN strategy TEXT;`
      2. Full test suite including integration + the config tests the laptop can't
         collect (`platformdirs` missing from the frozen laptop venv — pre-existing).
      3. `memai-bundle install bundles/italian-a0-starter` — expect persona created
         with strategy `language_tutor`, 46 inserted / 0 merged; re-run → 0 / 46.
      4. Live smoke: start server, switch to "Tutor Italiano"; verify (a) selection
         batch injected on the tutor's first turn (watch server log for the
         list_items query / injected item in a debug transcript), (b) two voices
         audibly alternate on [SPEAKER:] tags and rotate target voice across two
         sessions, (c) "just review old words" mid-session → [FOCUS:] marker, spoken
         acknowledgment, steered batch next turns, (d) response-language watch item
         from the prompt-pack entry above, (e) after disconnect+idle: consolidation
         writes persona_state (check concepts.persona_state in DB) and enrichment
         no-ops gracefully (no user-initiated cluster yet).
