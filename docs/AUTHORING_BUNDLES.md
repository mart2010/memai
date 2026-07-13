# Authoring Persona Bundles

Guide for authoring persona bundle content — written for the language-tutor case (the
first bundle-first persona) but the workflow generalizes to any curriculum-style persona.
This document is also the **seed requirements doc for the future authoring app**: every
manual pass described here is a candidate for automation in that (separate, out-of-repo)
project.

The bundle **file format** itself is specified in
[BRIEF_phase11_bundle_format.md](archive/BRIEF_phase11_bundle_format.md) — this guide assumes it
and covers the *content* side: what to put in the lessons and in what order.

## Ground rules the format enforces (and why)

The installer rejects bundles that violate these — author with them in mind:

- **No `engagement_level`, `persona_state`, or `embedding` on items.** A bundle cannot
  claim the user knows things (items always install as `UNSEEN`); `persona_state` is
  written only by the persona's own assessment strategy; embeddings are computed at
  install. Personalization is memai's job, not the bundle's — that user-independence is
  also what makes one bundle distributable to many users.
- **`description` is the primary knowledge carrier**: a tight synthesis, hard cap
  ~300 words (embedding-model input limit), written in the item's `language` — the
  language of first introduction, which stays fixed forever.
- **Insertion order is the contract.** Lessons install in filename sort order, items in
  file order, and curriculum order survives only as ascending database id. Name lesson
  files with zero-padded numeric prefixes (`01_…`, `02_…`); nothing else about a lesson
  is persisted (`title` is authoring metadata only).
- **`persona_key` is your namespace** (`<author>/<persona>`, e.g. `meo/spanish-tutor`).
  Unique by convention — pick a prefix you own and keep it stable across every bundle
  targeting the same persona (base level bundles, accelerators, later levels).
- **`strategy` names the runtime behaviour set** (optional `[persona]` key, e.g.
  `strategy = "language_tutor"`): it binds the persona to the selection/assessment/
  enrichment strategies registered in the memai server. Omit it for a plain
  conversational persona; an unknown name installs fine but binds nothing (warning at
  server startup). A language-tutor bundle should declare `"language_tutor"`.

## Pair-independence

Author **one main bundle per target language**, never per language *pair*:

- Main bundle: target-language-internal content only — vocabulary Concepts,
  `construction` Procedures, inflectional `morphological_pattern`s. The tutor LLM
  translates the explanatory wrapper live into the learner's language.
- **Cognate accelerators** are the pair-specific escape hatch: small optional bundles per
  source↔target pair (same `persona_key`, no `[persona]` table) carrying what genuinely
  depends on shared etymology or characters — cognate `morphological_pattern`s (`-tion` →
  `-ción`; shared Sino-Xenic characters for CJK pairs), L1-interference `contrast_pair` /
  `rules` items, cultural references. A thin accelerator for a distant pair (English →
  Japanese) is an honest content gap, not a defect.
- Pair-sensitive *settings* use learner-language-keyed maps with a `"*"` fallback (see
  `pair_difficulty` in the format brief) — resolved by the persona's own strategy at
  runtime, never by generic code.
- The native-teacher voice (`voices["default"]`) is **omitted** from the bundle and
  derived from `User.primary_language` at install. Any other cast voice is keyed by
  the target language's own IETF code (e.g. `voices["it"]`), not a role name —
  voice selection is automatic per synthesized segment from its own detected
  language, so the persona's own prompt only needs to say which language each
  speaker writes in, never a tag to emit (see `docs/spec/TECHNICAL.md` TR-305).

## Category taxonomy (tutor vocabulary)

`category` is free text interpreted by the owning persona; the tutor's settled taxonomy:

| Concept `category` | Meaning |
|---|---|
| `noun` / `verb` / `adjective` / `adverb` | single-word items; irregular verb forms are atomic `verb` Concepts (no generative rule to leverage); verb-class membership goes in the description text |
| `function_word` | high-frequency grammatical word — the highest-leverage early target: these anchor the most constructions |
| `idiom` | fixed non-compositional multi-word expression — ONE Concept, never decomposed |
| `contrast_pair` | a discrimination skill between already-known options, modeled as ONE Concept (`ser vs estar`, `tú vs usted`) |

| Procedure `category` | Meaning |
|---|---|
| `morphological_pattern` | word-formation rule, derivational or inflectional (conjugation paradigms fit naturally in `steps`) |
| `construction` | syntactic frame with open slots (`me gusta + [noun/infinitive]`) |
| `rules` | decision procedure for choosing among known forms (gender agreement, pronoun dropping) — governs choice, not generation |

## The multi-pass roster workflow

Author a bundle in ordered passes over a **roster** (the full ordered item list), not
item by item. Each pass is checkable in isolation — which is exactly what makes this
automatable later.

**Pass 1 — Roster.** Produce the complete ordered list of item *names* + `type` +
`category` + lesson grouping, before writing any descriptions. Scale reference: a CEFR
level ≈ 20–60 lesson files × 15–40 items ≈ ~600 items. Group lessons by communicative
function (greetings, ordering food — the functional-notional tradition), sequenced by
the lesson-ordering template below.

**Pass 2 — Ordering validation: the no-two-unknowns rule.** Walk the roster in order and
verify that every item combines **at most one new element with already-introduced
material** (Michel Thomas's principle). Concretely: a `construction`'s slots and example
sentences may only use Concepts appearing *earlier* in the roster; a `contrast_pair` may
only contrast options both already introduced; anchor sentences inside descriptions
reference known items only. Violations are fixed by reordering or by inserting the
missing prerequisite — never by teaching two unknowns at once.

**Pass 3 — Descriptions.** Generate each item's `description` (and `steps` where the
procedure decomposes cleanly), in the item's `language`, within the ~300-word cap. Anchor
informally in earlier roster items (plain text referencing them — deliberately no formal
links). Include a natural example sentence or two; a spontaneous mnemonic may live here.

**Pass 4 — Review.** Check category conformance against the taxonomy, deduplicate within
the bundle (the installer dedups against *installed* memory, not within your roster),
verify the ~300-word cap, and re-run the no-two-unknowns walk on the final text. Then
fill `[provenance]` honestly (`generator_model`, `authoring_workflow`, `generated_at`) —
months later it's the only way to trace a content-quality problem to its generator.

**Ephemeral generation (load-bearing).** Never pre-store what a Procedure can produce:
mastering `-tion → -ción` must not auto-populate a Concept per cognate, and a conjugation
paradigm does not ship one item per form. The bundle carries the *pattern*; generated
instances enter memory only if they organically surface in real conversation and are
picked up by ordinary consolidation. Keeps the store lean and matches the "you already
know thousands of words" pedagogy.

## Lesson-ordering template (MEO-BR)

The settled sequencing for a beginner language-tutor level:

1. **Cognate / cultural anchoring** — per-pair material, so in practice this is the
   companion accelerator bundle's job; the main bundle starts from zero-assumption items.
2. **Modal verbs** — small set, huge expressive leverage ("I want / can / must + verb").
3. **Base structures** — affirmation, negation, question formation (`construction`s).
4. **Frequent action verbs** — the core verb set the constructions operate on.
5. **Interest-driven thematic clusters** — food, travel, work…; runtime `propose_items`
   extends these from the user's actual interests, so bundle clusters are starting
   points, not exhaustive.
6. **Zipf top-1000 woven throughout** — high-frequency vocabulary distributed across all
   lessons rather than front-loaded as lists. Caution: "1000 words = 90%" is *token
   coverage*, not comprehension (~3000 word families for 95–98%) — never treat top-1000
   completion as a bundle completeness criterion.

## Requirements seed for the future authoring app

What the app must eventually automate, mapped from the passes above:

- Roster generation (pass 1) with lesson grouping and the ordering template as prompts.
- A mechanical **no-two-unknowns validator** (pass 2) — the highest-value automation:
  parse descriptions/steps/examples, resolve references against roster position, flag
  forward references.
- Batch description generation (pass 3) with per-item context = the roster prefix.
- Format emission + parse-and-reject validation against `format_version` (reuse memai's
  reader as the reference implementation — the format is the only coupling; the app must
  never import memai nor touch its DB).
- Provenance stamping per generation run.
- If per-user tailoring is ever wanted: consume an *exported knowledge-profile file*
  (a second file contract, deliberately not designed yet) — never DB access.
