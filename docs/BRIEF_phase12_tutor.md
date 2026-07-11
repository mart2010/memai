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
generates a word/sentence/form, the instance is **never stored as its own memory item**
unless it organically resurfaces in real conversation and gets picked up by ordinary
consolidation extraction. Mastering a pattern does not auto-populate Concepts for every
word/form it can produce. Keeps the DB lean and matches the "you already know thousands
of words, no memorizing" pedagogy.

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

The headline differentiator no standalone app can copy: the self-reference effect using
the user's own `Episode` store.

- The tutor **ELICITS** episodes ("tell me about a memorable meal") — the elicitation is
  itself the lesson (self-reference at encoding + production practice), seeds the Episode
  store, and reveals interests for `propose_items`. Triple duty.
- Same-session anchoring needs NO DB write — the story lives in the LLM context window
  (live working memory); it becomes cross-session material via ordinary consolidation.
- The selection strategy pairs each due item with an Episode via the **existing
  similarity search at session start** (consistent with fetch-once; lazy per-turn RAG
  recall coexists for utterance-triggered lookups). On a similarity miss it emits an
  **elicitation hint** in `SelectedItem.context` instead.
- Guardrails: cap **1–2 elicitation hints per session batch** (an LLM given ten hints
  will interrogate the user); prefer piggybacking on naturally arising topics; ramp with
  level — A0 elicitation is seeding + interest detection, not practice.
- **Extractor rule (implemented in Phase 10):** Episode summaries are ALWAYS written in
  `User.primary_language` regardless of conversation language — Episodes are
  persona-independent and carry no language field; months of tutoring must not turn the
  user's life story into target-language documents.

## Prompt-level pedagogy pack (no data-model impact)

- **Elicit-self-correction-then-recast** (Lyster & Ranta — prompts beat recasts for
  uptake): production before correction.
- **Pretesting / cognate guessing** (generation effect).
- **TPRS-style narrative co-construction** — story recap survives between sessions via
  ordinary Episode extraction.
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
  should count toward acquisition, but the ephemeral-generation principle means a
  rule-derived word can be noticed without being tracked — accepted tradeoff for DB
  leanness.

## Rejected — do not re-litigate without new evidence

| Rejected | Why |
|---|---|
| Formal anchor/lesson/CEFR relationships (FKs, link tables) | combinatorial growth; tested and rejected twice |
| Runtime LLM curriculum generation | no quality control, drifts between sessions — authoring-time bundles instead |
| Two-LLM-agent teachers | doubles local-GPU latency; one LLM call role-plays both |
| "Every word traced" absolutism (from the MEO BR) | ephemeral generation preserved — trace what surfaces in conversation |
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

## Pointers

- PLAN.md Phase 12 — the implementation checklist this brief backs.
- `docs/BRIEF_phase11_bundle_format.md` — bundle format + `InstallPersonaBundle` spec.
- `docs/AUTHORING_BUNDLES.md` — authoring guide + multi-pass strategy.
- PLAN.md Phase 10 — `category`, `persona_state` single-writer contract, `voices` map,
  strategy port signatures (`SelectedItem`, `MemoryItemDraft`, `ItemAssessment`).
