# Project Brief — Phase 11: Persona Bundle Format + `InstallPersonaBundle`

Design session 2026-07-10. Builds on the Phase 10 foundations (`category`,
`persona_state`, `voices` map, `MemoryItemDraft`, persona strategy ports) and the
language-tutor design record. Everything below is settled unless listed under Open
Questions.

## Context

Personas grow their long-term memory two ways: algorithmically (`propose_items`) or via
**bundles** — pre-packaged, curated content authored outside the conversational loop,
free or paid, distributed by persona developers. The language tutor (Phase 12) is
bundle-first: a CEFR level is one bundle; cognate accelerators are small per-language-pair
bundles. Phase 11 delivers the bundle file format and the installer; the first real
bundle and all tutor runtime machinery remain Phase 12.

## The central decision: the bundle file format IS the port

Authoring tools live **outside the memai repo** (own project, possibly commercial, one
per class of tutor — language, scientific, …). Memai never imports authoring code;
authoring tools never import memai. They meet at a **versioned, documented file format**
(`format_version = 1` from day one). Consequences:

- The generic/specialized split lives **in the format**: memai owns the envelope schema
  (manifest, persona-definition shape, item shape, lesson-as-ordering); the persona
  author owns all content vocabulary (`category` taxonomies, prompts, settings).
- The internal architecture of the authoring tool is **not designed now** — same rule as
  "no shared base class before a second real persona exists".
- **Personalization is memai's job, not the bundle's**: upsert-merge deduplicates against
  existing memory at install; the selection strategy skips retained material; interest
  adaptation is `propose_items`. Bundles stay user-independent — which is also what makes
  one bundle saleable to many users. If per-user authoring is ever needed, the path is an
  outbound knowledge-profile *file* (second file contract), never DB access from the tool.

## Bundle format (TOML directory)

```
spanish-a1/
  bundle.toml            # manifest
  lessons/
    01_greetings.toml    # ~15–40 [[items]] each; a CEFR level ≈ 20–60 lesson files
    02_ordering_food.toml
    ...
```

Read via stdlib `tomllib` (memai only ever reads bundles). Distribution as a plain
directory; zip packaging deferred.

### bundle.toml

```toml
format_version = 1                    # bundle-format contract version
persona_key = "meo/spanish-tutor"     # required, always

[bundle]
name = "spanish-a1"
version = "1.0.0"
author = "meo"
description = "Spanish A1 — functional-notional curriculum, ~600 items."

[provenance]                          # optional, free-form authoring metadata
generator_model = "claude-fable-5"    # content-quality attribution, months later
authoring_workflow = "meo-multipass/1"
generated_at = 2026-07-15

[persona]                             # OPTIONAL — required only when the persona may
name = "Profesora Sofía"              #   not exist yet; content-only bundles (e.g.
system_prompt = """..."""             #   cognate accelerators) omit it. Install fails
languages = ["es"]                    #   if persona absent AND no [persona] present.
response_language = "es"

[persona.voices]
target_teacher = "ef_dora"            # target-language roles: hardcodable (pair-indep.)
# "default" (native-teacher anchor) MAY be omitted → installer derives it from
# User.primary_language (same derivation as onboarding). Required by the entity
# invariant, so the installer supplies it before construction.

[persona.settings]                    # opaque — copied VERBATIM to AssistantPersona.settings
elicitation_cap = 2
target_voice_pool = ["ef_dora", "em_alex"]   # HVPT rotation set (Phase 12 reads it)

[persona.settings.pair_difficulty]    # tutor vocabulary: map keyed by LEARNER language,
en = 1.0                              # resolved by the tutor's own strategy at runtime
fr = 1.2                              # against User.primary_language — never by generic
"*" = 1.5                             # code; "*" = fallback. Keeps settings fully opaque.
```

### lessons/*.toml

```toml
title = "Greetings"                   # authoring metadata only — never persisted

[[items]]
type = "concept"                      # "concept" | "procedure"
name = "hola"
category = "function_word"            # persona-interpreted free text (Phase 10)
language = "es"                       # language of first introduction (existing invariant)
description = """..."""              # ~300-word cap invariant applies

[[items]]
type = "procedure"
name = "greeting someone politely"
category = "construction"
language = "es"
description = """..."""
steps = ["hola / buenos días", "¿cómo está?"]   # optional; procedures only
```

**Spec rules:**
- Items carry **no `engagement_level`** (installer always inserts `UNSEEN` — a bundle
  cannot claim the user knows things), **no `persona_state`** (single-writer contract),
  **no embedding** (computed at install).
- **Insertion order is the contract**: installers insert in lesson-filename sort order,
  then item order within each file. Curriculum order survives import as ascending SERIAL
  id; the Phase 12 selection strategy tiebreaks UNSEEN items by ascending id. Lessons
  leave no other persisted structure (2026-06-29 decision reaffirmed).
- **Pair-independence**: the main target-language bundle must not embed learner-language
  values. Pair-specific content (cognate `morphological_pattern`s, L1-interference
  `contrast_pair`/`rules`, cultural references) ships in per-pair accelerator bundles
  targeting the same `persona_key`; pair-specific *settings* use learner-language-keyed
  maps (see `pair_difficulty`); the native-teacher voice is derived at install.

## Persona identity: `persona_key`

Author-namespaced identifier (`meo/spanish-tutor`), unique **by convention** (no central
registry — a future marketplace can enforce prefix ownership on the same format).
Uniqueness only has to hold within one installation. New nullable+unique column
`personas.persona_key` (NULL for GA and user-created personas).

Install logic: persona with key exists → attach content; absent → create from
`[persona]` (error if absent too). **Upgrade semantics deferred**: if the persona exists
and the bundle also carries `[persona]`, the existing definition is kept untouched
(notice logged); overwrite-on-upgrade is decided when a real second-edition bundle exists.

## `InstallPersonaBundle` + `PersonaBundleSource`

- **Trigger**: new console script on the server package — `memai-bundle install <path>`.
  One-shot separate process (needs embedding model, DB, `memai.toml`); the session loop
  never calls or knows about it. Documented caveat: run while the server is idle (a
  concurrent consolidation run could race the same persona's upserts; single-user
  reality makes this documentation, not locking).
- **Pipeline reuse**: drafts flow through the existing embedding + 0.93/0.75
  merge-or-insert upsert — no separate insertion path. **Forced refactor**: extract the
  merge-or-insert machinery (`_merge_action`/`_existing_to_merge` + embedding +
  synthesis) from `ConsolidateMemory` into a shared upserter component used by both
  consolidation and the installer. Pure move, no behavior change.
- **Transactionality**: per-lesson `UnitOfWork` (mirrors per-conversation atomicity).
  Recovery = re-run the installer: already-inserted items merge into themselves
  (idempotent by merge). Optimization note: exact-duplicate short-circuit (same
  name + description → skip the LLM merge-synthesis call) makes reinstalls near-free.
- **On create**: `languages` = bundle's target list + `User.primary_language`;
  `voices["default"]` derived from `User.primary_language` when omitted;
  `[persona.settings]` copied verbatim to `AssistantPersona.settings`.
- **Provenance**: append-only `bundle_installs` log row per install (persona_key, bundle
  name/version/author, installed_at, items inserted/merged counts, `[bundle]` +
  `[provenance]` persisted verbatim as JSONB). Nothing reads it in any code path; it
  exists for "which installed content came from where/what generator" — same rationale
  as `origin_conversation_id`. Deliberately NOT a reinstall guard.

## Schema deltas (edit `001_initial_schema.sql` in place, per Phase 8 convention)

1. `personas.persona_key TEXT NULL` + `UNIQUE`
2. `personas.settings JSONB NULL` — opaque persona-owned tunables; read only by that
   persona's own strategies; generic code never branches on contents (same
   leak-prevention contract as `persona_state`, one level up)
3. New `bundle_installs` append-only table

## Non-goals (explicitly out of scope)

- The authoring tool/app itself (separate future project, own repo, own design session)
- Standalone validator CLI (`memai-bundle validate`) — future; installer parse-and-reject
  is Phase 11's only validation
- Quality/safety review pass for third-party content; marketplace/registry/trust
- Persona-definition upgrade/overwrite semantics on reinstall
- Zip/single-file bundle packaging
- Knowledge-profile export (the per-user authoring escape hatch)
- Runtime consumption of `settings` (pair-difficulty scaling, HVPT rotation, elicitation
  cap) — Phase 12

## Open questions (decide during Phase 11/12 implementation)

- Authoring guide doc: capture the multi-pass roster workflow (no-two-unknowns,
  ephemeral-generation, MEO-BR lesson-ordering template) as a document — it doubles as
  the seed requirements doc for the future authoring app. In Phase 11 scope as a doc,
  not code.
- `pair_difficulty` map conventions (`"*"` fallback key) are tutor vocabulary — finalize
  with the Phase 12 strategy, not in the generic format spec.
- `bundle.version` comparison semantics (semver?) — unused in Phase 11 (log-only).
- Exact `AssistantPersona.languages` union semantics at install.

## Recommended next step

Implement in this order: (1) schema + domain fields (`persona_key`, `settings`,
`bundle_installs`), (2) upserter extraction refactor (keeps consolidation green),
(3) `PersonaBundleSource` port + TOML reader adapter, (4) `InstallPersonaBundle` use
case + Fakes + unit tests, (5) `memai-bundle` console script, (6) hand-written
mini-bundle fixture + integration test (real Postgres, GPU workstation), (7) authoring
guide doc.
