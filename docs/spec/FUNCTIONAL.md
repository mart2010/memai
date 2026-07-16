# Functional Specification

*Last verified against code: 2026-07-16*

Externally observable behaviour, by capability. Terms per [GLOSSARY.md](GLOSSARY.md);
ID and wording conventions per [SPEC.md](SPEC.md). Technical contracts (protocol,
formats, algorithms) live in [TECHNICAL.md](TECHNICAL.md).

## FR-0xx — Onboarding & first launch

- **FR-001** On first server start, the single `User` row must be created automatically
  (no manual SQL); `primary_language` starts null.
- **FR-002** On client connect, when `User.primary_language` is null the server must
  send `select_language` listing the installed language codes (FR-705); the client must
  render a terminal selection prompt and reply `language_selected` with the chosen code.
- **FR-003** Completing language selection must persist the primary language and set the
  GeneralAssistant's `response_language` and default voice to the language's default
  Kokoro voice (fallback `af_heart` for unknown codes).
- **FR-004** Until language selection completes, incoming audio must be ignored; the
  client must mute the mic while the selection prompt is open.
- **FR-005** On the user's very first turn, the assistant must deliver a spoken
  introduction (in its own words) covering: placeholder name, fully-local/no-cloud,
  cross-conversation memory, personas, voice-only configuration, and that the
  introduction can be repeated on request.
- **FR-006** Onboarding is a once-only flow: subsequent sessions with a set primary
  language must skip language selection, and must then include memory brief and session
  tail (both skipped during the onboarding session).

## FR-1xx — Live conversation loop

- **FR-101** The user speaks; capture is hands-free: VAD detects speech, streams it, and
  end-of-utterance is inferred from trailing silence (~750 ms) — no push-to-talk.
- **FR-102** Each utterance must be transcribed with automatic language detection (no
  forced language); the detected language is recorded on the user turn.
- **FR-103** An utterance whose transcript is empty/whitespace must produce no response
  (the server still signals `speaking_end`).
- **FR-104** The reply must be synthesised and sent **incrementally** — sentence by
  sentence as the LLM streams — not after the full response completes.
- **FR-105** The assistant must respond in the active persona's `response_language`
  (instructed via system prompt) and speak at the persona's `speaking_rate` — with one
  exception: the GeneralAssistant mirrors the user, replying in the current utterance's
  detected language whenever it is an installed language (FR-705). Mirroring is per-turn
  and ephemeral — no stored setting moves (INV-14) — and never applies to strategy
  personas. Cast personas (non-default `voices` keys) receive **no** generic
  response-language instruction at all: a two-teacher cast deliberately speaks two
  languages per reply, and language use is owned by the persona's own system prompt.
- **FR-106** Spoken text must be cleaned for TTS: markdown emphasis/headers/rules and
  emoji stripped; digit sequences spelled out as words for languages where reliable
  (en, fr, es, it, pt), left to the TTS engine otherwise.
- **FR-107** No barge-in (INV-4): the client must keep the mic muted from the first
  audio chunk until the server signals `speaking_end`.
- **FR-108** A failure while processing one turn (e.g. TTS error) must not end the
  session or the server: the error is logged, the turn is dropped, `speaking_end` is
  still sent, and the session continues.
- **FR-109** Session-start context: the system prompt must include the memory brief
  (when one exists) and, when the previous session ended within 24 h, the last 10 turns
  of that session as a session tail.
- **FR-110** Long sessions must not overflow the context: every 50 turns, the oldest
  half of the recent-turn window is folded into a rolling summary that replaces those
  turns in the context.
- **FR-111** Every user and assistant turn must be appended to the session log at the
  moment it happens (the only live write, INV-1); assistant turns record the active
  persona and any conversation-boundary marker.
- **FR-112** The assistant must classify topic continuity via response prefix markers:
  `[TOPIC_BREAK]` splits conversations mid-session; `[TOPIC_CONTINUATION]` (valid only
  on a session's first turn) declares the session a continuation of the previous
  conversation. Markers are never spoken.
- **FR-113** When the GeneralAssistant is active and the detected utterance language is
  not an installed language, the reply must be in the user's primary language and must
  remind the user that the language is not installed and that re-running the install
  wizard (`memai-setup`) is how to add it.
- **FR-114** Every user turn must be rendered into the LLM context prefixed with its
  detected language as a `[lang:code]` tag (all personas) — during a tutor session the
  tag tells the model whether the learner produced the target language, spoke their own,
  or (a third language) likely stumbled on pronunciation; for the GA it is the evidence
  behind mirroring (FR-105). Tags are context-rendering only: stored turns and session
  logs stay clean (the log's `language` field carries the code, TR-402), and a
  `[lang:]` tag mimicked by the model in its response is stripped before TTS, never
  spoken.

## FR-2xx — Personas

- **FR-201** The GeneralAssistant must always exist (seeded, fixed id), is the persona
  every session starts on, and must be protected: it cannot be removed or deactivated.
- **FR-202** When more than one persona exists, the assistant must be able to switch by
  emitting `[PERSONA:name]` (matched case-insensitively against persona names) at the
  start of a response; the switch applies from that response onward. Unknown names or
  the already-active persona are a silent no-op.
- **FR-203** The available personas must be listed in the system prompt so the LLM can
  offer and perform switches conversationally.
- **FR-204** Persona management use cases exist for create, edit, list, remove,
  deactivate, reactivate — with the rules: creation only while GA is active; system
  personas cannot be removed/deactivated; removal cascades to the persona's concepts
  and procedures (INV-9); deactivation preserves memory for later reactivation.
  **⚠ Gap:** only *edit* (onboarding voice/language) and *switch* (`[PERSONA:]`) are
  wired to live triggers; create/remove/deactivate/reactivate have no voice or CLI
  entry point yet.
- **FR-205** A persona's voice identity is its `voices` map (mandatory `default`
  anchor, INV-7; other keys are IETF language codes). Each synthesized segment's own
  detected dominant language must switch the synthesis voice to that language's
  registered voice, at whole-segment granularity — never mid-sentence, so a segment
  quoting a foreign word in an otherwise native-language sentence stays in the
  native voice; an undetected or unregistered language falls back to the default
  anchor.
- **FR-206** A non-default `voices` map key defined as a `|`-separated pool must
  resolve to one voice per session — stable within the session, rotating across
  sessions (HVPT) — with no persisted state.

## FR-3xx — Memory & recall

- **FR-301** After conversations are consolidated (offline), the assistant must know
  their content in later sessions through three memory types: episodes (events),
  concepts (knowledge), procedures (know-how).
- **FR-302** Every substantive user turn must be checked against long-term memory (not
  gated on an explicit phrase like "remember when…" — FR-309 governs which turns count
  as substantive); the matching memories (top 5 by similarity; concepts/procedures
  scoped to the active persona, episodes global) must be injected into that turn's
  context.
- **FR-303** Concept/procedure knowledge must be persona-scoped end-to-end: the same
  name under different personas is different knowledge, with independent engagement.
- **FR-304** Repeated encounters must enrich, not duplicate: near-identical extracted
  items merge into the existing item (two-tier threshold), synthesising the description
  rather than appending; clearly-new items insert.
- **FR-305** Engagement must only ratchet upward on merge (max of existing and new).
- **FR-306** Episode summaries must always be written in the user's primary language,
  whatever language the conversation happened in (INV-10).
- **FR-307** Trivial exchanges must not become episodes: episode extraction is gated by
  a per-conversation worthiness judgment. Concepts/procedures are extracted regardless.
- **FR-308** A fresh memory brief must be generated after each offline run that
  consolidated at least one conversation, and be in place for the next session start.
- **FR-309** Whether a turn triggers a recall search at all is a persona-scoped policy
  (a `RecallGate`), not a fixed rule: the general assistant skips trivial short replies
  (a handful of words or fewer) since they carry no searchable content, while a persona
  for which short replies are meaningful — e.g. a language tutor, where "which word
  would you like to practice?" is answered with a single word — always searches
  regardless of length. Independently, a turn whose embedding is nearly identical to
  *any* prior search this session (for the active persona) — not only the most recent
  one — skips a fresh search and reuses that search's results: correct, not just an
  optimisation, because nothing new can enter long-term memory mid-session (INV-1), so
  a repeat of any earlier query would deterministically return the same thing again.

## FR-4xx — Offline processing & durability

- **FR-401** All heavy processing (DB writes, extraction, embedding for storage,
  upserts, brief generation) must happen offline, after disconnect (INV-1).
- **FR-402** The offline pipeline must start after the user has been disconnected for
  `User.idle_consolidation_minutes` (default 5); a reconnect within the window must
  cancel it (it will run after the next disconnect).
- **FR-403** The pipeline order is: replay session logs → consolidate conversations →
  enrichment proposals → regenerate memory brief.
- **FR-404** Crash safety: unprocessed session logs must be replayed into the DB on
  every client connect, so a crashed or killed server loses no conversation. Replay
  must be idempotent (already-persisted sessions skipped).
- **FR-405** A consolidation failure must lose nothing: each conversation is processed
  in its own transaction; a failed conversation remains unconsolidated and is fully
  reprocessed next run.
- **FR-406** Session logs are permanent (INV-5): no rotation, no cleanup.
- **FR-407** For personas with a registered assessment strategy, consolidation must
  only *recognise* practice against existing content (consolidation gates): no episodes
  extracted, no new items inserted, no curated wording rewritten — engagement bumps and
  category gap-fills only.

## FR-5xx — Language tutor (first strategy persona)

- **FR-501** On the tutor's first active turn of a session, a selection batch must be
  fetched (default mix: ~50% review items ranked least-known/stalest first, rest new
  items in curriculum order, interleaved by category); exactly one item is injected per
  turn until the batch is exhausted.
- **FR-502** A user session wish must steer selection: the LLM emits `[FOCUS: …]` with
  the wish verbatim; the strategy interprets it (mode review/new/mixed, a category, or
  a free topic ranked by similarity) and the batch is replaced accordingly. The marker
  is never spoken.
- **FR-503** Where a due item relates to a stored episode (similarity above the anchor
  threshold), the injection must carry that personal anchor; otherwise at most 2 items
  per batch may carry an elicitation hint inviting a short personal story.
- **FR-504** Nothing said during a lesson may enter long-term memory as new content
  (ephemeral generation): no new episodes, concepts, or procedures from tutor
  conversations (FR-407); elicited stories live only in that turn's context.
- **FR-505** After each lesson is consolidated, the tutor must update per-item SRS
  state from evidence: successful retrievals (not exposures) grow the half-life, errors
  shrink it, practice is day-granular, user-initiated items are flagged sticky and get
  a longer initial half-life, scaled by the configured pair difficulty.
- **FR-506** Selection must rank reviews by engagement level until the persona's
  settings switch it to retention ranking (derived decay estimate) — the
  instrument-now-calibrate-later posture.
- **FR-507** When several user-initiated concepts cluster around a theme, offline
  enrichment must propose the surrounding vocabulary cluster (one cluster per run,
  proposals installed as `UNSEEN` through the normal upsert dedup).
- **FR-508** Tutor lessons must be staged as a two-teacher cast (fixed native anchor +
  rotating target-language teacher) using FR-205/FR-206 mechanics.

## FR-6xx — Persona bundles (power-user extension)

- **FR-601** `memai-bundle install <path>` must install a bundle directory: creating
  the persona from its `[persona]` definition when the `persona_key` is new, or adding
  content to the existing persona otherwise (an existing persona's definition is kept;
  the bundle's is ignored with a notice — upgrade semantics deferred).
- **FR-602** Every bundle item must install at engagement `unseen` (INV-12); merging
  into an already-engaged item keeps the earned level (FR-305).
- **FR-603** Reinstalling the same bundle must be idempotent: unchanged items merge
  into themselves (near-free via the exact-duplicate short-circuit), nothing
  duplicates, user progress is preserved.
- **FR-604** Within one install run, a bundle's own items must never merge into each
  other (sibling exclusion) — the author meant them as distinct; matching against
  pre-existing content (earlier installs, live extraction) is intended behaviour.
- **FR-605** Installing a persona-creating bundle must fail with a clear error until
  onboarding is complete (the native-language anchor voice and input languages derive
  from `User.primary_language`).
- **FR-606** Curriculum order must survive install: lessons in filename order, items in
  file order, persisted as insertion order (INV-11) and honoured by tutor selection of
  new items.
- **FR-607** Each install run must append a provenance record (bundle identity,
  counts, manifest verbatim) that survives persona deletion.
- **FR-608** A malformed bundle must be rejected as a whole with a format error naming
  the offence (missing manifest keys, unsupported `format_version`, unknown `[persona]`
  keys, malformed items); nothing partial is installed from a bundle that fails parsing.
- **FR-609** Installing a persona-creating bundle must fail with a clear error — naming
  the missing language and pointing at `memai-setup` — when any of the bundle's target
  languages is not an installed language (FR-705): the session language pair is only
  speakable when the target's TTS voices actually exist on the machine, and that must
  surface at install time, not as a missing-voice failure mid-lesson.

## FR-7xx — Configuration & deployment

- **FR-701** Voice is the only configuration surface for the GeneralAssistant's own
  settings; every voice-configurable setting is a DB-backed attribute of `User` or
  `AssistantPersona` — `memai.toml` holds only bootstrap-before-DB settings. Anything
  requiring install/download/restart is wizard territory (`memai-setup`), not voice.
- **FR-702** The system must assume exactly one user: no authentication, no concurrent
  sessions, no row-level security (INV-3).
- **FR-703** The client must support both deployments from one config file: `ssh_host`
  present → auto-establish (and auto-restart) an SSH tunnel before connecting;
  absent → connect to the local server directly.
- **FR-704** Persistent language settings — `User.primary_language` and any persona's
  `response_language` — must only ever change on explicit user request (INV-14), never
  inferred from detected speech language. The GeneralAssistant's per-turn response
  mirroring (FR-105) is ephemeral and writes nothing.
- **FR-705** The install wizard must record the selected languages in `memai.toml`
  (`[languages].installed`) — the **installed languages**: the wizard-selected subset
  of supported languages whose TTS voices actually exist on the machine. Onboarding
  language selection (FR-002) and response mirroring (FR-105/FR-113) are bounded by
  this set; adding a language means re-running `memai-setup`. A config without the key
  (written before it existed) treats every supported language as installed.
- **FR-706** Re-running the install wizard must start from the recorded installation
  state, not from nothing: the existing `memai.toml` is parsed and its current settings
  shown up front; already-installed languages come pre-checked in the language
  selection (so adding one never silently drops the rest of `[languages].installed`);
  the current LLM and Whisper choices are the highlighted defaults (current LLM marked
  in its label); the recorded database connection is offered as a keep-current default
  (still verified). Topology is locked when inferable (`ssh_host` ⇒ split-host client),
  asked again otherwise; a malformed config degrades to a fresh run.
- **FR-707** The install wizard must let the user choose how live conversation is
  powered: local via Ollama (default), or a remote OpenAI-compatible HTTP endpoint
  (any provider — OpenRouter, OpenAI, a self-hosted server, ...), for installs without a
  local GPU capable of fast live inference. Minimal remote configuration: a base URL and
  a model name are required; an API key is optional (some self-hosted endpoints don't
  require one). This choice affects only the live conversational path (the main reply
  and per-turn recall-intent detection, TR-955) — the offline memory pipeline
  (consolidation, memory brief generation, and Ollama-backed persona-strategy helpers
  such as the language tutor's focus interpreter) always runs on a local Ollama model
  regardless, so a GPU-less install accepts it running slower, not remotely.
