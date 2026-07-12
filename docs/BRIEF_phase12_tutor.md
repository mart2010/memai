# Project Brief — Phase 12: Language Tutor Persona

Design record consolidated from multi-session work (2026-06-29 stress tests, extended
2026-07-10 after the MEO BR-doc challenge). This is the full design behind the PLAN.md
Phase 12 checklist. Everything below is settled unless listed under Deferred — the
guard-clause history at the end says what may and may not be reopened.

Goal framing that shaped every call: optimize for the user **speaking the new language
rapidly**, not literary/grammatical expertise.

## Context

The language tutor is the first concrete persona extension — the worked example for the
Phase 10 strategy ports (`PersonaSelectionPort`, `PersonaEnrichmentPort`,
`PersonaAssessmentPort`) and the first consumer of the Phase 11 bundle pipeline
(`docs/BRIEF_phase11_bundle_format.md`, `docs/AUTHORING_BUNDLES.md`). Bundles are the
curriculum backbone; `propose_items` supplements them with interest-driven clusters.

**Headline result of the stress tests: no new entities and no formal relationships were
needed.** `Concept` and `Procedure` (plus the `category` field added in Phase 10) covered
every example thrown at them across Spanish, French, and Japanese/Korean/Chinese cases,
and survived a cross-check against second-language-acquisition (SLA) theory.

Memai's actual target-language ceiling is Kokoro TTS's ~9-language coverage
(`en, fr, es, it, pt, ja, ko*, zh-cn` — see CLAUDE.md; ko was later dropped for lack of a
Kokoro voice), not the data model.

## Content model

### Concept `category` taxonomy

`Concept(name, description, language, category, engagement_level)` — `category` values
are informal, free-text, persona-interpreted; the taxonomy deliberately mixes
lexical-class and structural-shape axes:

| `category` | Meaning | Examples |
|---|---|---|
| `noun` | single-word noun | `el coche` (car), `la casa` (house) |
| `verb` | single-word verb (infinitive) | `comer` (to eat), `hablar` (to speak) |
| `adjective` | single-word adjective | `rojo/roja` (red), `grande` (big) |
| `adverb` | single-word adverb | `rápidamente` (quickly), `muy` (very) |
| `function_word` | high-frequency grammatical word (pronoun/preposition/connector) | `y` (and), `con` (with) — the highest-leverage early-teaching target, since these anchor the most `construction`s |
| `idiom` | fixed, non-compositional multi-word expression — one Concept, never decomposed | `tener hambre` (to be hungry), `echar de menos` (to miss someone) |
| `contrast_pair` | a discrimination skill between two+ already-known options, modeled as ONE Concept (not two Concepts + a link) | `ser vs estar`, `tú vs usted`, `por vs para`; generalizes to honorific/register systems (Japanese plain vs. polite verb forms) |

- **Irregular/exception verb forms** (French `être`, `avoir`, `aller`; Spanish `tener`)
  get no generative Procedure — taught directly as atomic `verb` Concepts, form by form,
  since there is no rule to leverage.
- **Verb-class membership** (e.g. "parler is a regular -er verb") is folded into the verb
  Concept's own `description` text, not a separate tracked field or relationship.

### Procedure `category` taxonomy

`Procedure(name, description, language, category, steps, engagement_level)` — anchored
**informally only**: the anchor sentence is text inside `description`, referencing
Concepts the learner already knows. Deliberately NOT a formal FK/relationship — rejected
twice on combinatorial-growth grounds (complex sentences would need many-Concept links).

| `category` | Meaning | Examples |
|---|---|---|
| `morphological_pattern` | word-formation rule (derivational OR inflectional) generating new word forms; instances stay ephemeral, never pre-stored | Derivational/cognate: English `-tion` → Spanish `-ción`, `-ous` → `-oso`. Inflectional: French `-er` conjugation paradigm (fits naturally in `steps`: `["je parle", "tu parles", ...]`). CJK equivalent: shared Sino-Japanese/Sino-Korean characters (電話/电话). |
| `construction` | syntactic frame with open slots generating new sentences from known pieces; can be simultaneously anchored AND generative (not mutually exclusive axes) | "estar + gerundio" (present progressive); "me gusta + [noun/infinitive]" (non-canonical/dative word order — confirms the category isn't limited to subject-verb-object frames); Japanese particle frames ("Xが好きです") |
| `rules` (naming to be finalized) | decision procedure for choosing among already-known forms — governs context-dependent choice, not generation | gender agreement (adjective endings matching noun gender, anchored informally in known adjective Concepts); subject-pronoun dropping in Spanish |

### Ephemeral-generation principle (load-bearing)

The most pressure-tested decision across both stress-test sessions: when a Procedure
generates a word/sentence/form, the instance is **never stored as its own memory item**.
Mastering a pattern does not auto-populate Concepts for every word/form it can produce.
Keeps the DB lean and matches the "you already know thousands of words, no memorizing"
pedagogy.

**Correction (2026-07-12, consolidation-scope decisions)**: this section originally
allowed one exception — "unless it organically resurfaces in real conversation and gets
picked up by ordinary consolidation extraction" — on the assumption that the generic
extractor could be trusted to insert a genuinely new, organically-surfaced form as its
own Concept. That assumption was wrong. Live testing against the actual local model
(`aya-expanse`) doing tutor-conversation extraction showed it invents fictional content
freely (a children's-story cat's actions turned into a literal "how to break into a
restaurant" Procedure with real steps) and cannot reliably separate a genuine personal
narrative from language-practice role-play when asked to judge after the fact — see
"Consolidation scope for the tutor" below. The exception is retired: the ephemeral
generation principle is now unconditional. Any word the learner needs tracked has to
either already exist (bundle/`propose_items`) or be raised through explicit self-report
("I already know X") — never through incidental resurfacing during a lesson.

### Consolidation scope for the tutor (2026-07-12, settled)

Generic offline consolidation (`ConsolidateMemory`) treats any persona with a registered
`PersonaAssessmentPort` (today, only the language tutor) differently from GA or a
fact-grounded persona like an astronomy tutor: it may **recognize**, never **author or
edit**. Reasoning: a language lesson's drills and role-play are not real events or new
facts, even when phrased in the first person, and the tutor already owns a dedicated,
purpose-built pipeline for both new content (bundles, `propose_items` interest-cluster
proposals) and engagement tracking (`persona_state` via the assessment strategy) — the
generic extractor has no business independently authoring either.

- **Episodes**: never requested at all for such a persona (`extract_episodes=False` —
  the shared extraction prompt omits the episodes schema section entirely, rather than
  asking then discarding). A prompt-engineering attempt to instead ask the model to
  judge "genuine story vs. practiced drill" was tried and made results worse, not
  better, on the same local model — not a wording problem, a reliability ceiling.
  Genuinely memorable events surface through the *existing* episodic-anchoring design
  instead (pairing a due item with an already-known GA-side Episode via similarity
  search, or an elicitation hint) — anchoring already-known material, never learning
  something new about the user's life from a lesson.
- **Concepts/Procedures — miss**: discarded, not inserted (`allow_insert=False`). New
  tutor vocabulary only ever comes from bundles or `propose_items`.
- **Concepts/Procedures — match**: `engagement_level` (and a `category` gap-fill) still
  updates as before — that's the legitimate "the user already knows/used this" signal,
  matching Anki-style self-report in spirit — but `update_description=False`: the
  synthesizer is never called and the description/steps are kept byte-for-byte from the
  existing (curated) record. A single conversation's phrasing must never drift a curated
  definition, regardless of how the match was reached.

Both gates key off the exact same generic signal `ConsolidateMemory` already had
(`assessment_strategies.get(persona_id) is not None`) — no tutor-specific vocabulary
leaks into the shared extractor or upserter; they only ever see plain booleans.

## CEFR / curriculum bundling — resolved without new structure

A CEFR level (A1, A2, B1...) is simply **its own installable bundle**. "Lessons" within a
level (A1.1, A1.2...) are an **authoring-time-only organizational convenience** (grouping
content by communicative function — greetings, ordering food — per the
functional-notional syllabus tradition) that controls import order; they leave no
persisted structure after import. Post-import sequencing falls out of `engagement_level`
+ informal textual anchoring. Beginner-vs-advanced overlap (`tú vs usted` → `tú vs usted
vs vos`) resolves via the existing similarity-based upsert pipeline, no formal
relationship.

Bundle lesson-ordering template (adopted from the MEO BR's pedagogy ordering):
cognate/cultural anchoring per pair → modal verbs → base structures
(affirmation/negation/question) → frequent action verbs → interest-driven thematic
clusters → Zipf top-1000 woven throughout. Caution: Zipf "1000 words = 90%" is **token
coverage, not comprehension** (~3000 word families for 95–98%) — do not encode 90% as a
bundle completeness criterion.

## Cross-language-pair robustness

- **Target-language-internal content is pair-independent**: vocabulary Concepts,
  `construction` Procedures, inflectional `morphological_pattern`s. Author ONE bundle per
  target language; the tutor LLM translates the explanatory wrapper live into the
  learner's native language. No N×M bundle explosion for the bulk of any curriculum.
- **Cognate-type `morphological_pattern`s are genuinely pair-specific** (shared
  etymology/characters between two specific languages) — author as small, optional
  **cognate accelerator** bundles per source↔target pair. Romance-Romance pairs get rich
  ones (Latin roots); CJK-CJK pairs get rich ones via a different axis (shared Sino-Xenic
  characters) — same Procedure shape. Some pairs (e.g. English → Japanese) have a
  thin/near-empty accelerator — an honest content-investment gap, not a model failure;
  absence of an accelerator bundle needs no special-casing.
- Per-pair difficulty coefficient is a **tutor persona setting** (the
  learner-language-keyed `pair_difficulty` map in `AssistantPersona.settings`, resolved
  by the tutor strategy at runtime), not per-item state.

## Spaced repetition — SRS via `persona_state`

The original 2026-06-29 simplification (engagement_level → implied review interval) was
upgraded 2026-07-10 on MEO-BR evidence. The tutor's `persona_state` dict (see
`project_persona_extension_ports` / PLAN.md Phase 10 for the slot's single-writer
contract) holds:

| Field | Notes |
|---|---|
| `last_practiced_at` | DAY granularity — sleep-gated spacing |
| `half_life_days` | grows on successful retrieval, shrinks on error; longer initial value when `user_initiated`; scaled by the pair-difficulty setting |
| `retrievals` | **successful retrievals only** (successive-relearning rule, NOT exposures) |
| `errors` | count |
| `avg_response_latency_s` | weighted low — end-of-TTS→speech-start at turn granularity is a noisy proxy; derivable offline from `Turn.timestamp` deltas in the JSONL logs, so no live/offline boundary impact |
| `user_initiated` | salience flag |
| `sessions_practiced` | count |

**Mastery and next-due are DERIVED at selection time** (exponential decay from
`last_practiced_at` with `half_life_days`) — never stored.

**Phasing (instrument now, calibrate later — same posture as the 0.93/0.75 upsert
thresholds):** the assessment strategy writes `persona_state` from day one;
`select_items` keeps ranking by `engagement_level` until real calibration data justifies
switching to retention ranking.

### Self-assessment gating

The tutor LLM occasionally solicits explicit self-assessment ("we've covered this a few
times, want fewer reminders?"); that feedback is **considered, not blindly trusted** by
offline consolidation, weighed against the extractor's own evidence of actual performance
(guards against illusory confidence from mere exposure — known metacognition result).
Two gating rules:

1. Assistant-**initiated** check-ins are gated by `engagement_level` — don't ask about
   something only just reached `MENTIONED`; no basis to judge yet.
2. User-**volunteered** self-assessment ("I already know hola") has no gate — always
   accepted and acted on immediately, weighed against evidence the same way.

This mirrors Anki's real-world self-rating mechanism more than abstract SM-2 timestamps —
a theory-consistent simplification.

## Enrichment — `propose_items` interest clusters

The 2026-06-29 bundle-only decision was reversed 2026-07-10 on the BR's interest-cluster
requirement: the tutor implements `PersonaEnrichmentPort.propose_items` after all.
Offline enrichment proposes the surrounding vocabulary cluster once consolidation shows
several user-initiated Concepts sharing a theme. Bundles remain the curriculum backbone.

## Two-teacher cast

- ONE persona, ONE LLM call role-playing both teachers (native-language teacher +
  target-language-only teacher) with **speaker-tagged segments**; the two-LLM-agent
  design was rejected — it doubles local-GPU latency.
- The TTS stage switches Kokoro voice per segment (cheap — one model). Uses the Phase 10
  `AssistantPersona.voices: dict[str, str]` map (speaker role → Kokoro voice; `"default"`
  role always present; GA keeps a single-entry map).
- **Target-teacher voice ROTATES across sessions** (HVPT — multi-voice phoneme exposure);
  native-teacher voice stays fixed as the anchor. The native-teacher `"default"` voice is
  derived from `User.primary_language` at bundle install when omitted.
- Vocabulary: this is a "cast" / "speaker roles" — deliberately distinct from
  `PersonaSwitch`; never conflate the two.

## Episodic anchoring + elicitation

**Corrected 2026-07-12 — see "Consolidation scope for the tutor" above.** The headline
differentiator no standalone app can copy: the self-reference effect using the user's
own `Episode` store — but that store is populated exclusively from GA-side
(native-language) conversation, never authored fresh during a tutor lesson. Episodes
are never extracted from tutor conversations at all (`extract_episodes=False`), so the
earlier framing below — that elicitation "seeds the Episode store" and "it becomes
cross-session material via ordinary consolidation" — no longer holds and is retired.

- The selection strategy pairs each due item with an **already-existing** Episode via
  the **existing similarity search at session start** (consistent with fetch-once; lazy
  per-turn RAG recall coexists for utterance-triggered lookups). On a similarity miss it
  emits an **elicitation hint** in `SelectedItem.context` instead.
- The tutor still **ELICITS** ("tell me about a memorable meal") — but purely as
  **production practice**: self-reference boosts recall of the vocabulary/construction
  being practiced, and prompts genuine target-language output. Whatever the user says
  stays in the LLM context window for that turn only and is never captured as a new
  Episode or Concept — nothing new is *learned* about the user's life from a lesson, by
  design (see the retired ephemeral-generation exception above). A genuinely new,
  memorable thing the user mentions is for a future GA conversation, in their own
  language, to capture as an Episode in the ordinary way — not this lesson.
- Interest signals for `propose_items` come from user-initiated **Concepts** (the
  assessment strategy's salience flag), not from Episode content — no change needed
  here, this was already the actual implementation.
- Guardrails: cap **1–2 elicitation hints per session batch** (an LLM given ten hints
  will interrogate the user); prefer piggybacking on naturally arising topics; ramp with
  level — A0 elicitation is seeding + interest detection, not practice.
- Extractor rule (Phase 10): Episode summaries are always written in
  `User.primary_language` — still true for GA and any persona that does extract
  episodes; moot for the tutor, which never requests them.

## Prompt-level pedagogy pack (no data-model impact)

- **Elicit-self-correction-then-recast** (Lyster & Ranta — prompts beat recasts for
  uptake): production before correction.
- **Pretesting / cognate guessing** (generation effect).
- **TPRS-style narrative co-construction** within a single session — still valid as a
  live technique. **Cross-session story recap is retired as of 2026-07-12**: it
  previously relied on the co-constructed story surviving via ordinary Episode
  extraction, which no longer runs for tutor conversations (see "Consolidation scope for
  the tutor" above). No replacement persistence mechanism exists today — flagged as an
  open gap, not silently dropped; a session-recap could be re-added later as its own
  narrow, tutor-owned mechanism (e.g. persisted in `AssistantPersona.settings` or a
  dedicated field) if this is judged worth it, rather than by reopening generic Episode
  extraction.
- **Interleaved session batches by `category`** (anti-blocking) — one rule in the
  selection strategy.
- The production effect validates voice-only as a strength, not a limitation.

## Theory validation (all consistent with SLA literature)

- `EngagementLevel` ladder ≈ Skill Acquisition Theory's
  declarative → proceduralized → automatized pipeline.
- `construction` ≈ Construction Grammar (Goldberg), almost exactly.
- `idiom`-as-one-Concept ≈ formulaic-language/chunking theory (Wray) — fluent speech
  leans on stored chunks, which also serves the "speak rapidly" goal directly.
- `contrast_pair`-as-one-Concept ≈ standard minimal-pair/contrastive pedagogy.
- Pimsleur's anticipation-pause/output mechanic ≈ Swain's Output Hypothesis + the testing
  effect — pure live-conversation behavior, no data-model impact.
- One acknowledged tension: the Noticing Hypothesis (Schmidt) says conscious exposure
  should count toward acquisition, but the ephemeral-generation principle (now
  unconditional, 2026-07-12) means ANY word — rule-derived or genuinely novel — can be
  noticed in conversation without being tracked. Accepted tradeoff, widened from the
  original DB-leanness rationale to also cover extraction reliability (see
  "Consolidation scope for the tutor" above): explicit self-report ("I already know X")
  is the sanctioned path for surfacing this kind of signal, not incidental resurfacing.

## Rejected — do not re-litigate without new evidence

| Rejected | Why |
|---|---|
| Formal anchor/lesson/CEFR relationships (FKs, link tables) | combinatorial growth; tested and rejected twice |
| Runtime LLM curriculum generation | no quality control, drifts between sessions — authoring-time bundles instead |
| Two-LLM-agent teachers | doubles local-GPU latency; one LLM call role-plays both |
| "Every word traced" absolutism (from the MEO BR) | ephemeral generation preserved, now unconditional (2026-07-12) — nothing that merely surfaces in tutor conversation is traced |
| Zipf-90% as bundle completeness criterion | token coverage ≠ comprehension |
| Keyword mnemonics | retrieval practice beats them long-term; a spontaneous mnemonic can live in `description` |
| True shadowing | impossible under no-barge-in mic muting; delayed echo-imitation is fine |
| TPR, gamification | not applicable / out of scope |

The 2026-06-29 decisions (SRS simplification, bundle-only) WERE legitimately reopened
2026-07-10 on MEO-BR evidence; those reopenings are now themselves settled.

## Deferred (not Phase 12)

- The external authoring app (own project; format-is-the-only-coupling, see the Phase 11
  brief).
- Half-life function calibration (instrument now, switch ranking later).
- `persona_episode_state` association table — only if per-persona state on shared
  Episodes ever becomes real (e.g. retelling counts).
- Per-segment voice switching beyond the two-teacher cast; standalone bundle validator
  CLI; quality/safety review pass; persona-def upgrade semantics; knowledge-profile
  export (all listed in the Phase 11 brief).

## Phase 12 wiring decisions (settled 2026-07-12, implemented)

Three gaps surfaced when mapping this design onto the Phase 10 wiring; all closed in
the "wiring foundation" step (see PLAN.md Phase 12 for implementation detail):

- **Strategy binding**: `AssistantPersona.strategy: str | None` (e.g.
  `"language_tutor"`), declared in the bundle's optional `[persona] strategy` key and
  resolved against a composition-root registry in `server.py`. New tutor bundles bind
  without code changes; unknown names warn and bind nothing.
- **Lazy, focus-aware selection batches**: the Phase 10 eager fetch at session start
  could never fire for a tutor (sessions start on GA; tutors arrive via `[PERSONA:]`
  switch). Batches are now fetched lazily by `ProcessTurn` on the persona's first
  active turn, held per persona in `WorkingMemory.selection_batches`. The port became
  `select_items(persona_id, focus: str | None = None, limit=10)` — the speculative
  `category`/`engagement_level` filters were dropped; `focus` carries the user's
  expressed session wish verbatim ("just review old vocabulary today"), interpreted
  only by the strategy; `focus=None` = resume the default curriculum.
- **`[FOCUS: ...]` marker**: when the user states or changes what they want this
  session, the tutor LLM emits the marker as a response prefix (combinable:
  `[PERSONA:X][FOCUS: ...]` applies to X) and should verbally acknowledge the shift
  ("let me pull up your review items") since the re-fetched batch serves the
  *following* turns. Generic code strips the marker and passes the payload verbatim —
  the inbound mirror of `SelectedItem.context`.
- **`MemoryRepository.list_items`**: non-similarity listing (persona, types, optional
  category/engagement filters) ordered by ascending id — the query surface the
  selection strategy ranks over.
- **Cast mechanism (implemented)**: inline `[SPEAKER:role]` tags in the LLM response
  switch the Kokoro voice per segment in the streaming path; unknown roles fall back
  to the default anchor. **HVPT rotation is a generic voices-map semantic**: a
  non-default role's value may be a `"|"`-separated pool (`"ef_dora|em_alex"`),
  resolved to one voice per session from the session id (stateless — the live path
  never writes). This supersedes the earlier `settings.target_voice_pool` sketch,
  which would have required generic code to read inside the opaque settings. The
  `"default"` (native-anchor) voice must be a single voice — entity invariant.

## Pointers

- PLAN.md Phase 12 — the implementation checklist this brief backs.
- `docs/BRIEF_phase11_bundle_format.md` — bundle format + `InstallPersonaBundle` spec.
- `docs/AUTHORING_BUNDLES.md` — authoring guide + multi-pass strategy.
- PLAN.md Phase 10 — `category`, `persona_state` single-writer contract, `voices` map,
  strategy port signatures (`SelectedItem`, `MemoryItemDraft`, `ItemAssessment`).
