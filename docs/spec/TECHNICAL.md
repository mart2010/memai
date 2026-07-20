# Technical Specification

*Last verified against code: 2026-07-16*

Internal contracts: architecture, protocol, formats, data model, algorithms. Terms per
[GLOSSARY.md](GLOSSARY.md); conventions per [SPEC.md](SPEC.md). File references anchor
each area to its implementation.

## Invariants

Cross-cutting hard rules. Violating any of these is always a defect; changing one is a
design discussion, never a patch.

- **INV-1 — Live/offline boundary.** The live conversation path may read the DB
  (session start, recall, lazy selection fetch) but writes only JSONL session logs. DB
  writes, embedding generation for storage, LLM extraction/synthesis, upserts, and
  brief generation happen only in the offline pipeline or the bundle installer.
- **INV-2 — Dependency rule.** `domain/` imports nothing external; `services/` imports
  domain only and defines all ports; `infrastructure/` implements ports. Inner layers
  never import from outer ones.
- **INV-3 — Single user.** One `User` row, one connection at a time, no auth. Design
  decisions may rely on this.
- **INV-4 — No barge-in.** The response plays to completion; the mic re-opens only on
  `speaking_end`.
- **INV-5 — Session logs are kept forever.** No rotation or cleanup logic anywhere.
- **INV-6 — Persona-state opacity.** `persona_state` is written only by the owning
  persona's assessment strategy (via `update_persona_state`; upserts structurally
  exclude the column) and read only by that persona's selection strategy.
  `AssistantPersona.settings` has the same opacity one level up. No generic code path
  branches on the contents of either. `Concept.directive` (FR-207) is the deliberate
  opposite: the one field generic code (`ProcessTurn`) is meant to read and act on —
  not persona-authored opaque data, but GA's own operational routing.
- **INV-7 — Voices anchor.** Every `voices` map contains the `"default"` role, and its
  value is a single voice (never a `|` pool). Enforced by the entity.
- **INV-8 — Description/embedding co-update.** On any content change to a
  concept/procedure description (or episode summary), the embedding is recomputed in
  the same operation. Under consolidation gates neither changes (both kept verbatim).
- **INV-9 — Persona scope & cascade.** Concept/procedure similarity search during
  upsert is always scoped to the owning persona. `ON DELETE CASCADE` from personas to
  concepts/procedures is load-bearing (clean persona teardown), not an oversight.
- **INV-10 — Episodes are persona-independent and primary-language.** Episodes carry no
  persona scope or language field; summaries are always written in
  `User.primary_language`. Personas with an assessment strategy never produce episodes
  at all.
- **INV-11 — Insertion order is curriculum order.** Ascending SERIAL id (within a
  type) / `created_at` (across types) is the contract bundle install writes and tutor
  selection reads. Nothing may reorder or renumber installed content.
- **INV-12 — Content sources never claim knowledge.** Bundle items and enrichment
  proposals always enter at `UNSEEN`; only generic consolidation moves engagement.
- **INV-13 — Language of first introduction is fixed.** `Concept.language` /
  `Procedure.language` never changes on upsert; descriptions stay in that language.
- **INV-14 — Persistent language settings switch explicitly only.** No implicit persona
  switching, and no change to any stored language setting (`User.primary_language`, a
  persona's `response_language`), from detected speech language.
- **INV-15 — Episode provenance is fixed.** `origin_conversation_id` is NOT NULL and
  never reassigned; `happened_at` is the temporal anchor, not the conversation date.

## TR-0xx — Architecture & composition

- **TR-001** Monorepo of three independent uv-managed packages: `client/` (capture &
  playback), `server/` (pipeline + memory), `setup/` (install wizard). Each has its own
  venv; Python ≥ 3.13.
- **TR-002** Server layout (`server/src/memai_server/`): `domain/` (entities, value
  objects, events, domain protocols), `services/` (use cases + ports), `infrastructure/`
  (Postgres, Ollama/OpenRouter, STT/TTS/embedding, JSONL, bundle TOML, per-persona
  strategy packages e.g. `language_tutor/`). INV-2 governs imports.
- **TR-003** `server.py` is the composition root: loads config, builds all adapters
  once (single long-lived process), wires use cases per connection.
- **TR-004** Strategy registries (`SELECTION/ASSESSMENT/ENRICHMENT_STRATEGY_FACTORIES`)
  map strategy-set names (e.g. `"language_tutor"`) to factories. Bindings are resolved
  per connection (selection) / per offline run (assessment, enrichment) by scanning
  personas' `strategy` fields — a bundle installed between sessions binds without a
  server restart. Unknown names log a warning and bind nothing.
- **TR-005** One registry entry serves every persona of that class (any target
  language); everything persona-specific comes from the persona's rows and `settings`,
  never from strategy code.
- **TR-006** Two independent Postgres connections: `conn` for the live path (event-loop
  thread), `offline_conn` for the background pipeline thread — a shared connection
  would interleave live queries into the offline transaction or block the loop.
- **TR-007** `ConsolidateMemory`, `EnrichMemory`, and replay are synchronous by design
  and must be dispatched via `asyncio.to_thread` when called from the event loop; brief
  generation streams async and is awaited directly.
- **TR-008** The one `User` row is bootstrapped by the server on startup when missing.

## TR-1xx — WebSocket protocol

`ws://localhost:<ws_port>` (default 8765), `max_size=None`.

| # | Message | Direction | Semantics |
|---|---|---|---|
| **TR-101** | binary frame | client→server | PCM **int16** 16 kHz mono; accumulated in the utterance audio buffer (ignored until onboarding is done) |
| **TR-102** | `{"type": "end_utterance"}` | client→server | Flush the buffer to STT and run the turn; ignored when the buffer is empty or onboarding incomplete |
| **TR-103** | `{"type": "select_language", "supported": [codes]}` | server→client | Sent on connect iff `User.primary_language` is null; `supported` = the installed languages (TR-951 `[languages]`), not all of `SUPPORTED_LANGUAGES` |
| **TR-104** | `{"type": "language_selected", "language": code}` | client→server | Completes onboarding (FR-003); ignored once onboarding is done |
| **TR-105** | binary frame | server→client | PCM **float32** 16 kHz synthesised audio, one frame per synthesised segment |
| **TR-106** | `{"type": "speaking_end"}` | server→client | Sent after each turn's chunks (even when the turn produced nothing); re-enables client VAD |

- **TR-107** Unparseable text frames are ignored; unknown `type` values are ignored.
  (Forward-compatible: new message types must not break old peers.)

## TR-2xx — Client

`client/src/memai_client/client.py`; stateless beyond one config file.

- **TR-201** Config: `memai.toml` in the platform config dir
  (`platformdirs.user_config_dir("memai")`), `[server]` table: `ws_port` (default
  8765), optional `ssh_host`. Missing file → clear `FileNotFoundError` guidance.
- **TR-202** Capture: `sounddevice` InputStream, 16 kHz mono float32, blocksize 480
  (30 ms); each block converted to int16 (`×32768`) for VAD and the wire.
- **TR-203** VAD: `webrtcvad`, aggressiveness 2. Speech frames are sent immediately;
  silence frames are not sent.
- **TR-204** End-of-utterance: after speech has been active, **> 25 consecutive silent
  frames** (~750 ms) sends `end_utterance` and resets. Silence before any speech sends
  nothing.
- **TR-205** Mic muting: an event flag suppresses the VAD callback from the first
  received audio chunk until `speaking_end` (and while the onboarding prompt is open);
  playback is blocking (`sd.play` + wait) per chunk.
- **TR-206** SSH tunnel (split-host): `ssh -N -L {ws_port}:localhost:{ws_port}
  {ssh_host}` as a daemon-thread subprocess, restarted in a loop 3 s after exit. Proxy
  env vars (`HTTP(S)_PROXY`) are stripped from the process environment.
- **TR-207** Onboarding UI: `questionary.select` terminal dropdown over the
  `supported` codes; falls back to the first entry on cancel.

## TR-3xx — Server live turn pipeline

`services/session.py` (`StartSession`, `ProcessTurn`, `EndSession`).

- **TR-301** `StartSession` loads: `User`, GA persona, GA's Directive concepts
  (FR-207), memory brief (skipped during onboarding), and the session tail — previous
  session's last `session_tail_turns` (default 10, `[server]` config, TR-951) turns
  iff it ended within `session_continuation_threshold_hours` (24). Fails fast when
  User or GA is missing.
- **TR-302** Turn sequence: STT → log user turn → directive matching (TR-315) →
  recall gate (TR-314; embed turn text, `search` top 5, persona-scoped; skipped on a
  turn that just switched persona) → lazy selection fetch/consume (TR-306; likewise
  skipped) → compose working context → stream LLM → per-sentence TTS → resolve
  markers → log assistant turn → rolling-summary check.
- **TR-303** Working-context composition (`_compose_working_context`): system prompt =
  persona prompt ⊕ onboarding directives (first launch) ⊕ response-language
  instruction (the active persona's fixed `response_language`; suppressed entirely for
  cast personas — non-default `voices` keys — whose own prompt owns language use,
  FR-105) ⊕ memory brief ⊕ recalled memories. No persona-listing/switch-instruction
  block (FR-202/FR-203 retired, FR-207) — the system prompt never names another
  persona. Messages = session tail (as one system message) ⊕ rolling summary ⊕ recent
  turns; a selected item is injected as a system message immediately before the
  current user turn. User turns (recent and tail) are rendered with their
  detected-language `[lang:code]` prefix (`_render_turn_content`, FR-114) —
  rendering-only, stored content untouched; a turn without a recorded language renders
  untagged.
- **TR-304** Response prefix grammar: `[FOCUS: wish]` is scanned for anywhere within
  the first `_PREFIX_SCAN_WINDOW_CHARS` characters of the response (not only at
  position zero — real models routinely preface a tag-bearing reply with
  conversational lead-in, e.g. an apology or acknowledgment, that a leading-only check
  can never see past), optional; `[TOPIC_CONTINUATION]|
  [TOPIC_BREAK]` is then checked at the start of whatever remains, only at response
  start. `[TOPIC_CONTINUATION]` outside a session's first turn is swallowed (not
  spoken, no event). If the LLM stream ends before the scan window closes,
  resolution force-finalizes against exactly what's buffered rather than waiting for
  tokens that will never come.
- **TR-305** Cast voice selection is per-segment and language-based, not tag-based:
  after prefix resolution, each complete sentence is classified by its own dominant
  language (`LanguageDetector.detect`, candidates restricted to `User.primary_language`
  plus the active persona's non-default `voices` keys) and a confident match switches
  the synthesis voice for that segment via `_session_voice`; a low-confidence result
  (see TR-307) keeps whatever voice was already active rather than forcing a switch.
  Deliberately whole-segment, never mid-sentence — a sentence quoting a foreign word
  stays entirely in the voice it started in.
- **TR-306** Selection batches: fetched lazily on a strategy persona's first active
  turn (`select_items(persona_id)`, a live DB *read*), stored per persona id in working
  memory; one item popped per turn; exhausted batches are not re-fetched. A `[FOCUS:]`
  marker re-fetches with the wish verbatim, replacing the batch — applied **after** any
  persona switch in the same response, so combined markers steer the target persona.
- **TR-307** Voice resolution (`_session_voice`): `voices[language_code]` (fallback
  default anchor for an unregistered or undetected code); `|`-pools pick index
  `session_id.int % len(pool)` — deterministic per session, no state. Detection
  (`infrastructure/language_detection.py`) returns `None` — leaving the current voice
  unchanged — below a minimum confident-length threshold (placeholder pending real
  tuning); very short segments (a bare greeting, "No.") are genuinely ambiguous to
  statistical language ID.
- **TR-308** Sentence-level synthesis: segments split on `.` `!` `?`, including
  retroactively splitting a multi-sentence chunk that arrives in one piece (e.g. via
  TR-304's force-resolve) into its constituent sentences rather than only checking
  whether the whole chunk ends on one; each segment is markdown/emoji-stripped,
  cleansed of any mimicked `[lang:]` tag (FR-114 — models can imitate the inbound
  user-turn convention), and number-spelled (num2words for en/fr/es/it/pt only) before
  TTS; empty segments are skipped.
- **TR-309** Rolling summary: when `total_turn_count % 50 == 0`, the oldest 25 recent
  turns are LLM-summarised into (or merged with) `rolling_summary` and dropped from the
  window.
- **TR-310** `[RETIRED 2026-07-18 — replaced by directive-based switching, TR-315]`
- **TR-311** Latency instrumentation: `[latency]` stdout lines for STT, first LLM
  token, first TTS chunk, total-to-first-audio, total turn, inter-turn gap.
- **TR-312** Turn timestamps: the user turn is stamped at `end_utterance` receipt; the
  assistant turn is stamped when the LLM stream finishes — the assistant→user delta is
  the stored response-latency proxy consumed by tutor assessment (TR-806).
- **TR-313** `[RETIRED 2026-07-18 — see FR-113]`
- **TR-314** Recall gating (`RecallGate`, FR-309): replaces the earlier per-turn LLM
  classification call (`RecallIntentDetector`, retired) with local, deterministic
  logic — no LLM call, no JSON parsing, no dependency on `[llm].provider`.
  `should_embed(text)` runs first, before any embedding is computed: `DefaultRecallGate`
  (used by GA and any persona without an override) returns `False` for utterances under
  `min_words` (3), a word-count floor chosen over a character count since it tracks
  "is there searchable content here" consistently across languages;
  `LanguageTutorRecallGate` overrides this to always return `True` — a tutor session's
  single-word vocabulary answers are exactly the content worth searching. When
  `should_embed` is `True`, the turn's raw text (not an extracted query — there is no
  extraction step anymore) is embedded and compared, via `domain.model.cosine_similarity`
  (in-process, no DB round trip), against **every** entry in
  `WorkingMemory.recall_history[persona_id]` — the embedding and results of every
  utterance that actually triggered a real search for the active persona this session,
  oldest first — not only the most recent entry: nothing new can enter long-term memory
  mid-session (INV-1), so a repeat of any earlier query, not only the immediately
  preceding one, would deterministically return the same results again.
  `should_search(max_similarity)` receives the highest similarity across that whole
  history (`None` if the history is empty — always search on the first turn) and
  returns `True` when it is `None` or below `dedup_threshold` (0.93, reusing the
  existing merge-threshold "same thing" bar); at or above it, the DB round trip is
  skipped and the best-matching historical entry's cached results are reused as-is,
  appended as a new entry to `recall_history` only when a real search actually runs.
  `search()` is called with every `MemoryType` (no type restriction — the old
  detector's type classification is not replaced by an equivalent, since the existing
  top-5-by-similarity ranking already surfaces the right items across types). Gate
  resolution mirrors `selection_strategies`: `ProcessTurn._recall_gates.get(persona_id,
  default_recall_gate)` — the one difference from `PersonaSelectionPort` et al. being
  that every persona resolves to *some* gate, never a no-op.
- **TR-315** Directive matching (`ProcessTurn.execute` step 3b, FR-207): runs
  unconditionally on every non-empty turn, independent of `RecallGate` (TR-314) — a
  directive phrase is a short command, exactly what `should_embed`'s word-count floor
  is designed to skip, so this computes its own embedding via a second, separate
  `EmbeddingService.embed()` call (accepted cost of keeping the two systems decoupled;
  not shared with recall's own embedding even on a turn where both end up embedding).
  The turn's embedding is compared via `domain.model.cosine_similarity` against every
  entry in `WorkingMemory.directive_concepts` (fetched once at session start,
  `MemoryRepository.list_directives(GENERAL_ASSISTANT_ID)` — same fetch-once posture
  as `memory_brief`); the highest-similarity match is used if it clears
  `_DIRECTIVE_MATCH_THRESHOLD` (0.85, placeholder pending live tuning, same posture as
  the 0.93/0.75 upsert thresholds). A clearing match whose `directive["action"]` is
  `"switch_persona"` and whose `target_persona_id` differs from the currently active
  persona (already-active is a silent no-op) calls the existing `SwitchPersona` use
  case (`services/persona.py` — previously defined but never invoked from any live
  code path; this is its first real caller), which updates `WorkingMemory.active_persona`
  and returns the `PersonaSwitched` event carried through to `TurnResult`. Recall
  (TR-314) and the persona-selected-item lazy fetch (TR-306) are both skipped entirely
  on a turn where a directive fired — the user's utterance was a directive, not a
  content question; both resume normally next turn under the new persona. The
  assistant turn is logged under the **new** persona id, same as the retired
  `[PERSONA:]` scheme (TR-310) guaranteed.

## TR-4xx — Session logs & replay

`infrastructure/json_file.py`, `services/replay.py`.

- **TR-401** One JSONL file per session: `logs/sessions/YYYY-MM-DD_<session_id>.jsonl`
  (date = first append, UTC).
- **TR-402** Turn line: `{"ts": iso, "speaker": "user"|"assistant", "content": str}` +
  optional `"language"` (user turns), `"marker"` (`"break"|"continuation"`), and
  `"persona_id"` (assistant turns). Close line: `{"type": "session_closed", "ts": iso,
  "clean_exit": bool}`.
- **TR-403** Replay grouping (`_group_into_conversations`): a `continuation` marker on
  the session's first assistant turn → the whole session extends the last open
  conversation in the DB (when none exists, it is saved as new); a `break` marker on a
  later assistant turn closes the current group (inclusive) and starts a new one; a
  `break` on the first assistant turn is ignored (a new session is already a boundary).
  Each group's persona is the persona of its first assistant turn (default GA).
- **TR-404** A file without `session_closed` (crash) is still replayed; the group's
  `ended_at` falls back to its last turn timestamp.
- **TR-405** Replay idempotency: files are scanned newest-first and the scan stops at
  the first session already persisted (monotonic invariant — older files are
  necessarily persisted); unprocessed sessions are then replayed oldest-first.
- **TR-406** Replay runs on every client connect (before the session starts) and as
  step 1 of the offline pipeline.

## TR-5xx — Data model & schema

`server/migrations/001_initial_schema.sql`, `domain/model.py`.

- **TR-501** PostgreSQL 15+ with pgvector; all embeddings `vector(1024)`
  (`multilingual-e5-large`); HNSW cosine indexes on episodes/concepts/procedures.
- **TR-502** Tables: `users` (singleton), `personas`, `conversations`, `turns`
  (composite PK conversation_id+timestamp; `session_id` indexed for replay
  idempotency), `episodes`, `concepts`, `procedures`, `bundle_installs` (append-only),
  `memory_brief` (singleton, `id = 1`).
- **TR-503** FKs: concepts/procedures → personas `ON DELETE CASCADE` (INV-9);
  conversations → personas `ON DELETE RESTRICT`; turns → conversations
  `ON DELETE CASCADE`; episodes → conversations (provenance, INV-15).
- **TR-504** `EngagementLevel` is an ordered enum `unseen(0) < mentioned(1) <
  explored(2) < integrated(3)`; stored lowercase text; extracted items default to
  `mentioned`.
- **TR-505** `SUPPORTED_LANGUAGES` = the faster-whisper ∩ Kokoro intersection,
  currently `en fr es it pt ja zh-cn` (7; `ko` dropped — no Kokoro Korean pipeline).
  The installed languages (FR-705) are the wizard-selected subset of this list; the
  composition root intersects `[languages].installed` with it (unsupported codes warn
  and are ignored; an empty intersection fails startup; key absent → all supported).
- **TR-506** GA seed: fixed UUID `00000000-0000-0000-0000-000000000001`, `is_system`,
  idempotent insert; `persona_key`/`strategy` are set at creation only and never
  updated by `save()` (like `is_system`).
- **TR-507** `Conversation` aggregate rules: no turns after `ended_at`; consolidation
  requires ended + non-empty + not already consolidated (`mark_consolidated` enforces).
- **TR-508** `list_items` orders by ascending id within each memory type
  (curriculum-order read side, INV-11) and rejects episode queries; `list_all()`
  returns **all** personas, including deactivated ones. *(Note: accepted for now;
  revisit when lifecycle gets a live trigger, FR-204.)*
- **TR-509** `search` returns `(similarity, item)` with **similarity = 1 − pgvector
  cosine distance**, merged across requested types, sorted, truncated to `top_n`;
  `persona_id` filters concepts/procedures only (episodes are global). Concepts with
  `directive` populated (FR-207) are excluded — they're GA's own operational routing
  data, not RAG content, and are never surfaced as a "Relevant memories" injection.
- **TR-510** `concepts.directive JSONB` (FR-207, nullable): `NULL` for an ordinary
  concept. `MemoryRepository.list_directives(persona_id)` returns every concept for
  that persona with `directive IS NOT NULL`, ascending id; `delete_concept(concept_id)`
  is a plain unconditional delete, used only by `PersonaDirectiveSync.sync_removed`
  (INV-9's cascade doesn't cover a GA-owned directive referencing a *different*,
  removed persona).

## TR-6xx — Upsert pipeline

`services/upsert.py` (`MemoryUpserter`) — the single shared merge-or-insert path for
consolidation, enrichment, and bundle install.

- **TR-601** Pipeline: embed (`"{name}: {description}"`; episodes embed the summary) →
  similarity search (concepts/procedures: top 5 persona-scoped; episodes: top 1
  global) → threshold decision → merge or insert.
- **TR-602** Two-tier thresholds: similarity ≥ `merge_threshold` (default 0.93) →
  auto-merge; ≥ `disambiguate_threshold` (default 0.75) → LLM binary disambiguation;
  below → insert. Configured via `[memory]` in `memai.toml`; defaults are calibration
  placeholders.
- **TR-603** Merge behaviour: LLM synthesis replaces the description (and steps),
  embedding recomputed (INV-8); engagement = max (FR-305); existing `category` wins,
  new one only fills a gap; item id adopted from the existing row.
- **TR-604** Exact-duplicate short-circuit: identical name + description (+ steps)
  skips synthesis and re-embedding — the reinstall fast path.
- **TR-605** `exclude_ids` (concepts/procedures): candidates with these ids are
  filtered out before the threshold decision — the bundle installer passes its own
  same-run insertions (sibling exclusion, FR-604). Top-5 candidate fetch exists so an
  excluded sibling cannot hide a real pre-existing match. Default empty; live
  consolidation never passes it.
- **TR-606** Curated-content protection: `update_description=False` → a match keeps
  description, steps, and embedding verbatim (only engagement/category move) —
  `bundle_install` and `EnrichMemory` pass this for procedure upserts (FR-407); a
  discarded candidate (see TR-609) leaves `item.id` as `None`, the *discarded
  sentinel* callers must check before further use.
- **TR-607** Each `upsert_*` mutates the passed item in place and returns `True` on
  merge, `False` on insert-or-discard (disambiguated by the id sentinel).
- **TR-608** Episode merge synthesises the two summaries and re-embeds;
  `origin_conversation_id` is never reassigned (INV-15).
- **TR-609** `upsert_concept` is origin-aware (FR-310): candidates are searched as
  usual (TR-601/602), then split by `Concept.origin`. If the best *authored* candidate's
  similarity ≥ `authored_protection_threshold` (default 0.75, its own constant — not
  `disambiguate_threshold`, since it answers "protect curated content" rather than "same
  entity") the candidate is written as a touch on that authored item — verbatim
  description/embedding, engagement bumped, `update_description` ignored. Otherwise the
  normal two-tier merge/insert (TR-602) runs scoped to *organic* candidates only. A
  brand-new organic insert (`existing is None`) additionally requires `_has_engagement`:
  at least `min_concept_engagement_turns` (default 2) of the caller-supplied
  `user_turns` (raw text) must literally name the concept (`_mentioned_in` — whole-word,
  case-insensitive regex match on the candidate's `name`, also matching a parenthetical
  abbreviation split out of it, e.g. "XAI" out of "Explainable AI (XAI)"). Embedding
  cosine-similarity between a turn and the candidate's own embedding was the original
  design (`concept_engagement_similarity`, reusing TR-807's `interest_cluster_threshold`)
  but was replaced 2026-07-20 after a live-testing finding: several sibling concepts
  extracted from one assistant monologue on a shared topic (e.g. AI/XAI/NLP/Transfer
  Learning) sit close together in embedding space, so a couple of broadly-on-topic user
  turns satisfied the similarity bar for *all* of them, not just the one actually
  followed up on — literal mention is the intersection of "what the conversation
  introduced" with "what the user's own words actually named," which similarity-to-topic
  could not express. `user_turns=None` (the default; `bundle_install`/`EnrichMemory`
  never pass it) skips this check entirely — only live-conversation extraction has turns
  to evaluate. `upsert_procedure` has no origin-awareness: procedures are always
  authored (FR-307), so `update_description=False` from the caller is their only
  protection.

## TR-7xx — Offline pipeline

`services/memory.py`, `server.py`.

- **TR-701** Trigger: on disconnect, an idle task sleeps
  `idle_consolidation_minutes × 60` then runs the pipeline; a new connection cancels
  the pending task. Pipeline exceptions are logged, never fatal.
- **TR-702** Order: replay (TR-406) → `ConsolidateMemory` → `EnrichMemory` →
  `GenerateMemoryBrief` (only when ≥ 1 conversation was consolidated). All against the
  offline connection (TR-006).
- **TR-703** Per conversation, one transaction wraps: extraction-floor check (FR-307;
  below floor, `mark_consolidated(worthiness=False)` and nothing else runs) → worthiness
  evaluation → extraction (`extract_episodes = strategy is None`, INV-10; never
  requests procedures) → episode upserts (worthy only, FR-307) → concept upserts
  (origin/engagement-gated, TR-609, FR-310 — independent of `worthy`) → assessment
  dispatch → `mark_consolidated`. Any raise rolls the whole conversation back for full
  reprocessing (FR-405).
- **TR-704** Assessment dispatch: after upserts, `assess_items(persona_id,
  conversation, touched)` where `touched` = items that hold an id (discarded misses
  excluded); returned `ItemAssessment.persona_state` dicts are persisted byte-for-byte
  via `update_persona_state` (INV-6).
- **TR-705** Enrichment: per persona with a strategy, `propose_items` drafts are forced
  to `UNSEEN` (INV-12); concept drafts get `origin="authored"` (FR-310); procedure
  drafts are upserted with `update_description=False` (FR-407) — both in one
  transaction per persona batch.
- **TR-706** Extraction language rule: the extractor receives
  `User.primary_language` and must write episode summaries in it (INV-10);
  `extract_episodes=False` omits the episode request from the prompt entirely. The
  `"procedures"` schema key is never requested, for any persona (FR-307) —
  `ExtractionResult` has no `procedures` field.
- **TR-707** Extraction floor (FR-307): `ConsolidateMemory` counts only
  `Speaker.USER` turns. Below `min_user_turns` (default 2) or below `min_user_words`
  (default 40, summed across those turns' `str.split()` word counts) the conversation
  skips worthiness evaluation and extraction entirely — `mark_consolidated(worthiness=False)`
  and no LLM call is made. Both are plain constructor defaults on `ConsolidateMemory`
  (calibration placeholders, like `merge_threshold`), not `[memory]`-configured.
- **TR-708** Worthiness/extraction content-quality rules (FR-307): the shared
  `WORTHINESS_SYSTEM_PROMPT` (`infrastructure/llm/_common.py`, used verbatim by both the
  Ollama and OpenRouter evaluators) explicitly excludes discussion about the assistant's
  own operation (bugs, testing, debugging, capability/configuration questions) from
  "worth storing." The episode section of `_extraction_system_prompt` requires a
  genuine, identifiable time or place, distinct from "during this chat"; `_parse_extraction`
  backs this with a hard rule — an episode with a missing or unparseable `happened_at`
  is dropped, never backdated to the conversation's own timestamp.

## TR-8xx — Language-tutor strategies

`infrastructure/language_tutor/` — reference implementation of the three ports.

- **TR-801** All tutor tunables live in `AssistantPersona.settings` (opaque, INV-6)
  with in-code defaults marked as calibration placeholders:
  `ranking` (`"engagement"`* | `"retention"`), `batch_review_share` (0.5),
  `episode_anchor_threshold` (0.6), `elicitation_cap` (2),
  `initial_half_life_days` (1.0), `half_life_growth` (2.0), `half_life_shrink` (0.5),
  `user_initiated_boost` (2.0), `pair_difficulty` (map keyed by learner language,
  `"*"` fallback), `interest_cluster_threshold` (0.55), `interest_cluster_min_size`
  (3), `enrichment_batch_size` (5).
- **TR-802** Selection pools: new = `UNSEEN` items in curriculum order (`created_at`,
  id); review = engagement > `UNSEEN`, ranked least-known-then-stalest (engagement
  mode) or ascending retention `2^(−days/half_life)` with missing/invalid SRS state
  most due (retention mode).
- **TR-803** Batch composition: focus topic → similarity-ranked over the mode's pool;
  focus mode review/new → that pool; default mixed → `review_share` of the limit from
  review, rest new, each backfilling the other. Then category interleave (round-robin,
  first-appearance order, stable within category). Focus category filters items but
  falls back to all when it would empty the session.
- **TR-804** Focus interpretation: the verbatim wish + the persona's actually-present
  category values go to a `FocusInterpreter` LLM returning `TutorFocus(mode, category,
  topic)` — the interpreter can only target real taxonomy values.
- **TR-805** Episode pairing: per batch item, top-1 episode similarity ≥ threshold →
  anchor context with the episode summary; otherwise an elicitation hint, at most
  `elicitation_cap` per batch (FR-503).
- **TR-806** Assessment: per touched item, an LLM `PracticeJudge` yields
  `(retrievals, errors, user_initiated)` matched back by name (no judgment = exposure
  only, which still moves the day anchor); current stored `persona_state` is re-read
  from the repository (upsert output never carries it); conversation-mean
  assistant→user latency (TR-312) is folded in weighted low. Half-life: grows
  `×growth` on successful retrieval, shrinks `×shrink` on error, floor 0.5 days,
  initial value boosted for user-initiated items and scaled by pair difficulty;
  `last_practiced_at` is the conversation's date (day granularity).
- **TR-807** Enrichment: seeds = user-initiated concepts (flag from SRS state) with
  embeddings; greedy single-pass cosine clustering at `interest_cluster_threshold`;
  qualifying clusters need `min_size`; **one cluster per run** (largest); cluster
  language = majority vote; the `ClusterProposer` LLM returns up to
  `enrichment_batch_size` drafts.
- **TR-808** The tutor's SRS `persona_state` vocabulary is exactly the `state.py`
  constants (glossary: *SRS state*); mastery/next-due are always derived, never stored.

## TR-9xx — Persona bundle format

`infrastructure/bundle_toml.py`, `services/bundle_install.py`, `bundle_cli.py`. The
format **is** the port (version `BUNDLE_FORMAT_VERSION = 1`); parse-and-reject is the
only validation layer.

- **TR-901** A bundle is a directory: `bundle.toml` manifest + `lessons/*.toml`
  (≥ 1 required), lessons ordered by filename sort.
- **TR-902** Manifest: `format_version` (must equal 1), `persona_key` (non-empty
  string), `[bundle]` table with required `name`, `version`, `author` (+ optional
  `description`), optional `[provenance]`; `[bundle]`+`[provenance]` are persisted
  verbatim to the install log (TR-905).
- **TR-903** Optional `[persona]` table: `name`, `system_prompt`, `languages`,
  `response_language`, `voices` (may omit `default` — derived from
  `User.primary_language` via the composition root's language→voice map), optional
  `settings` (copied verbatim) and `strategy`. Unknown keys are rejected.
- **TR-904** Install algorithm: resolve persona by `persona_key` (create from
  `[persona]` when absent — requires completed onboarding AND every bundle target
  language installed (FR-609, checked against `resolve_installed_languages` of the same
  `[languages].installed` the server reads); error when neither persona nor definition
  exists; existing persona + `[persona]` → notice, definition ignored). Persona
  `languages` = bundle list + primary language appended iff absent — the *session
  language pair*. Then per lesson, one transaction;
  per item, upsert as `UNSEEN` with same-run sibling exclusion (separate id sets per
  memory type). A failed run is recovered by re-running (committed lessons merge via
  TR-604).
- **TR-905** Install log: one `bundle_installs` row per run — persona_key (plain text,
  survives persona deletion), bundle identity, timestamps, inserted/merged counts,
  manifest verbatim. Read by nothing; not a reinstall guard.
- **TR-906** `memai-bundle install <path>` runs as its own process (own DB connection,
  embedding model, config); documented caveat: run while the server is idle (a
  concurrent consolidation could race the same persona's upserts — documentation, not
  locking, per INV-3).

## TR-95x — Configuration & models

- **TR-951** Server config `memai.toml` (platform config dir; wizard-generated):
  `[server] ws_port=8765, log_dir=<platform data dir>/sessions` (absolute,
  `platformdirs.user_data_dir("memai")` — session logs are persistent data, INV-5, not
  settings; user-overridable via this same key), `session_tail_turns=10` (FR-109;
  0 disables session-tail injection entirely — not voice-configurable, FR-701 doesn't
  apply, same technical-tuning-knob posture as `[memory]`'s thresholds below);
  `[database] url` (libpq DSN; peer
  auth default on Linux/macOS); `[stt] model_path, device cuda|cpu, compute_type`
  (float16↔cuda, int8↔cpu); `[tts] device` (absent → Kokoro auto-detect); `[llm]
  model="aya-expanse", ollama_host?` (always: the local Ollama model for the offline
  pipeline, TR-955), plus `provider="ollama"|"openai_compatible"` (default `"ollama"`,
  omitted from written configs in the common case), and, only when `provider =
  "openai_compatible"`: `base_url`, `remote_model` (both required), `api_key` (optional)
  — the live-conversation backend, FR-707; `[memory] merge_threshold=0.93,
  disambiguate_threshold=0.75`; `[languages] installed=[codes]` (the installed
  languages, FR-705 — absent → all of `SUPPORTED_LANGUAGES`). Bootstrap-before-DB
  settings only (FR-701); installed languages qualify because they are a property of
  the installation (which TTS voices were pulled), changeable only via the wizard.
- **TR-952** Models: STT `faster-whisper` (beam 5, auto language); LLM via Ollama
  streaming, or an OpenAI-compatible remote endpoint for live conversation only
  (TR-955) — avoid ~70B-class Ollama models (VRAM eviction/cold-reload; avoid
  reasoning models — `<think>` blocks get spoken); TTS Kokoro (one lazily-created
  pipeline per voice-prefix language, output resampled 24 kHz → 16 kHz float32);
  embeddings `intfloat/multilingual-e5-large` (1024-dim, always local/CPU-or-GPU —
  never remote, TR-955).
- **TR-953** Offline LLM-backed judgment adapters (worthiness, disambiguation,
  synthesis, extraction, tutor judge/cluster) all use the same configured local Ollama
  model/host, regardless of `[llm].provider` (TR-955). OpenRouter twins of this offline
  family exist in `infrastructure/llm/openrouter.py` but are not wired into the
  composition root (deployment-alternative groundwork, distinct from the live
  OpenAI-compatible pair that TR-955 documents as wired in).
- **TR-954** Outbound TLS uses the OS trust store (`truststore`) — corporate-proxy
  resilience for any adapter that still touches the network.
- **TR-955** Live conversation's main reply (`ProcessTurn`'s call to
  `LLMService.complete()`) is the only part of the pipeline `[llm].provider` affects
  (FR-707): `"ollama"` (default) uses `OllamaLLMService` against `[llm].model`/
  `ollama_host`, same as before this setting existed; `"openai_compatible"` uses
  `OpenAICompatibleLLMService` (`infrastructure/llm/openai_compatible.py`, a generic
  `openai.AsyncOpenAI` client against `[llm].base_url`, any OpenAI-compatible provider —
  OpenRouter, OpenAI, a self-hosted server; a missing `api_key` is coalesced to a
  placeholder string, since the client library requires some value even against
  endpoints that don't check it) against `[llm].base_url`/`remote_model`/`api_key`.
  Recall gating (TR-314) does *not* move with this setting — it stopped being an LLM
  call at all when `RecallIntentDetector` was retired in favour of `RecallGate`, so it
  no longer has a live-provider dimension to branch on. Every other LLM-touching port
  (offline judgment adapters, TR-953; `GenerateMemoryBrief`, via a dedicated
  `ServerContext.offline_llm` instance never aliased to the live `llm`; Ollama-backed
  persona-strategy helpers, e.g. the tutor's focus interpreter) always stays on the
  local Ollama model, unaffected by this setting — a GPU-less/CPU-only offline run is
  accepted as slower, not moved off-machine.
