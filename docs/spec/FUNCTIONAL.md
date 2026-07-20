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
  (instructed via system prompt) and speak at the persona's `speaking_rate` — this
  applies uniformly, GeneralAssistant included (its `response_language` is set to
  `User.primary_language` at onboarding, FR-003, and only changes on explicit request,
  INV-14). Cast personas (non-default `voices` keys) receive **no** generic
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
- **FR-113** `[RETIRED 2026-07-18 — GA response-language mirroring removed;
  GA.response_language is now a plain fixed setting like every other persona's (FR-105),
  with no detection-driven override or not-installed reminder]`
- **FR-114** Every user turn must be rendered into the LLM context prefixed with its
  detected language as a `[lang:code]` tag (all personas) — during a tutor session the
  tag tells the model whether the learner produced the target language, spoke their own,
  or (a third language) likely stumbled on pronunciation. Tags are context-rendering
  only: stored turns and session logs stay clean (the log's `language` field carries the
  code, TR-402), and a `[lang:]` tag mimicked by the model in its response is stripped
  before TTS, never spoken.

## FR-2xx — Personas

- **FR-201** The GeneralAssistant must always exist (seeded, fixed id), is the persona
  every session starts on, and must be protected: it cannot be removed or deactivated.
- **FR-202** `[RETIRED 2026-07-18 — replaced by directive-based switching, FR-207. The
  `[PERSONA:name]` LLM-emitted tag scheme required listing every other persona's name
  in the system prompt to get the model to notice it reliably, which turned out to
  also be exactly the kind of salience that caused language drift toward that other
  persona even when nobody asked to switch — see the language-drift discussion FR-207
  replaces this with.]`
- **FR-203** `[RETIRED 2026-07-18 — persona discovery moved to onboarding/FAQ; GA's
  system prompt no longer names another persona under any circumstance, the actual
  fix for the drift FR-202 caused.]`
- **FR-204** Persona management use cases exist for create, edit, list, remove,
  deactivate, reactivate — with the rules: creation only while GA is active; system
  personas cannot be removed/deactivated; removal cascades to the persona's concepts
  and procedures (INV-9); deactivation preserves memory for later reactivation.
  Create and remove additionally sync a GA-owned Directive concept via
  `PersonaDirectiveSync` (FR-207) — INV-9's cascade doesn't cover it, since it's
  GA-owned, not the removed persona's own. **⚠ Gap:** only *edit* (onboarding
  voice/language) and *switch* (FR-207) are wired to live triggers;
  create/remove/deactivate/reactivate have no voice or CLI entry point yet.
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
- **FR-207** A **Directive** is a user utterance that changes memai's own operating
  state rather than being answered in conversation. Persona switching is the first
  (and, currently, only) directive type; a Directive is represented as a GA-owned
  `Concept` with its `directive` field populated (e.g.
  `{"action": "switch_persona", "target_persona_id": "<uuid>"}`), matched by embedding
  similarity against the turn's own utterance — not by anything the LLM decides or
  emits. A clearing match executes deterministically **before** that turn's system
  prompt is composed: `active_persona` changes first, so the reply is generated by the
  new persona from the first token, and the system prompt never has to name the other
  persona to make the switch happen (the actual fix for the language drift the retired
  `[PERSONA:]` tag scheme, FR-202, used to cause). An already-active target is a silent
  no-op (carried over from FR-202). Canonical trigger phrasing is documented for the
  user (README/FAQ) and is exactly what gets embedded into the matching Directive
  concepts — both sides of the similarity computation anchor on the same wording, to
  keep match precision high. `PersonaDirectiveSync` keeps directive concepts in sync
  with persona create/remove (FR-204) and bootstraps the fixed "return to the
  GeneralAssistant" directive idempotently on every server startup.

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
- **FR-307** Trivial exchanges must not become episodes or concepts: a cheap
  deterministic floor (minimum user turns and minimum user words, counting only the
  user's own turns — assistant chatter must never inflate it) gates whether worthiness
  evaluation and extraction are even attempted at all; below it, both are skipped
  outright, purely for cost control. Above the floor, episode extraction is separately
  gated by a per-conversation worthiness judgment (LLM) whose criteria explicitly
  exclude discussion about the assistant's own operation (bugs, testing, capability
  questions) and require a genuine, identifiable time or place — a substantial
  conversation is not automatically an episode-worthy one. Procedures are never
  extracted from conversation, for any persona: how-to knowledge belongs to authoring
  expertise (bundles) or persona enrichment, never live discussion.
- **FR-310** Concept creation from live conversation is gated independently of episode
  worthiness, by origin and engagement, not by a whole-conversation verdict. Every
  Concept carries an immutable `origin` ("authored" — bundle install, persona
  enrichment — vs "organic" — live extraction). A live-extraction candidate close
  enough to an existing *authored* concept is recognised as a touch on it (engagement
  bump only, content immutable) regardless of which persona the conversation belongs
  to; a candidate distinct from all authored content is free to merge into or insert as
  a new *organic* concept using the existing merge/disambiguate thresholds (FR-304) —
  but a genuinely new organic insert additionally requires real user engagement: the
  user must have literally named it in at least two of their own turns, not merely been
  present for an assistant mention or a single follow-up question — topical proximity
  to what the assistant introduced is not engagement.
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
  never extract episodes or author new procedures from conversation — their lessons are
  practice, not autobiography or authored how-to knowledge — and must never rewrite
  curated (authored) wording: engagement bumps and category gap-fills against existing
  content only. Concept creation is not persona-gated the same way: it follows the same
  origin/engagement rules as any other persona (FR-310), so a user going genuinely
  off-curriculum mid-lesson can still produce a new organic concept — curated content
  stays immutable either way, via FR-310's authored-origin protection, not via a
  blanket ban on strategy personas ever authoring anything new.

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
- **FR-504** Nothing said during a lesson may enter long-term memory as new curated
  content: no new episodes or procedures from tutor conversations (FR-407); elicited
  stories live only in that turn's context. A concept distinct from curriculum content
  and clearing the engagement gate (FR-310) is the one exception — genuine
  off-curriculum discussion, not lesson drills, which never carry that kind of
  real-world content to begin with.
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
  inferred from detected speech language.
- **FR-705** The install wizard must record the selected languages in `memai.toml`
  (`[languages].installed`) — the **installed languages**: the wizard-selected subset
  of supported languages whose TTS voices actually exist on the machine. Onboarding
  language selection (FR-002) is bounded by this set; adding a language means
  re-running `memai-setup`. A config without the key (written before it existed) treats
  every supported language as installed.
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
