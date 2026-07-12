# Personas — one assistant, many specialists

Most voice assistants are one personality with one job. Memai is built differently: a
single private memory engine that can host **multiple specialised assistants** — personas
— each with its own expertise, its own voices, and its own slice of your long-term
memory. You switch between them by voice, mid-conversation, and each one picks up
exactly where you left off with *it*.

## Why a persona is more than a system prompt

Anyone can prepend "You are a helpful tutor" to an LLM call. What makes a Memai persona
a genuine specialist is what sits underneath:

### Its own memory scope

Knowledge is **persona-scoped**. The same words mean different things in different
contexts — "big bang" is cosmology to an astronomy tutor and a sitcom to a pop-culture
companion. Memai stores each persona's Concepts and Procedures separately, so knowledge
never bleeds or blends across specialists. How well you know something is scoped too:
you may have fully integrated a concept with one persona and only brushed past it with
another, and each persona sees its own picture.

### Clean lifecycles

Personas can be temporary by design. Preparing for one exam, one trip, one project?
Spin up a persona for it, and when it's over, delete it — **all of its knowledge goes
with it**, in one stroke, leaving your personal memory untouched. Deactivate instead of
delete, and it waits, memory intact, until you need it again.

### Its own voice cast

Each persona carries a **voice map**: named speaker roles resolved to local TTS voices.
A simple persona has one voice. A richer one can stage a cast — the language tutor
role-plays two teachers in a single response, and the audio switches voices per segment
as they trade lines.

### Its own learning strategies (for the ambitious ones)

Advanced personas plug directly into the memory engine through three strategy ports:

- **Selection** — what should this session work on? (live, at conversation time)
- **Assessment** — how did the user actually do? (offline, after the session)
- **Enrichment** — what new content is worth proposing next? (offline)

The **General Assistant** — the default, cross-domain persona — uses none of them and
works beautifully. A specialist like the tutor uses all three.

### Curated content via bundles

A persona can be seeded with a **content bundle**: a curated, versioned package of
concepts and procedures authored *outside* the conversation loop — by a human who knows
the domain, not by an LLM improvising a curriculum at runtime. Bundles install
idempotently, never claim you already know anything (everything starts `unseen`), and
re-install cleanly on top of your progress. Power users can author their own — see
[Authoring bundles](AUTHORING_BUNDLES.md).

---

## The Language Tutor

The first specialist to ship is a **second-language tutor** — and it is not a chatbot
with a French accent. Every design decision in it traces back to established
second-language-acquisition (SLA) and memory research.

### Two teachers, many voices

A lesson is staged as a dialogue between two teachers: a **native-language teacher**
(your anchor — explains, encourages, keeps you oriented) and a **target-language
teacher** who speaks only the language you're learning. One LLM plays both; the audio
switches voice per speaker.

The target teacher's voice deliberately **rotates across sessions** while the anchor
stays fixed. That is not a gimmick — it is **high-variability phonetic training
(HVPT)**: a robust experimental finding that hearing the same sounds from *multiple
speakers* builds dramatically better phoneme perception than hearing one voice, however
clear.

### Spaced repetition that respects the science

The tutor tracks each word, construction, and idiom you practice with a per-item memory
state — when you last retrieved it, how its retention half-life is growing, how often
you've succeeded and stumbled. Reviews are scheduled from that decay model, with
**day-level granularity** because consolidation is sleep-gated: what matters is nights
between retrievals, not minutes.

Two research-backed details most flashcard apps get wrong:

- Progress counts **successful retrievals, not exposures** (the *successive relearning*
  paradigm) — seeing a word ten times is worth less than recalling it twice.
- Your own "I already know this" is **considered, not blindly trusted** — self-assessed
  confidence is weighed against actual performance, guarding against the well-documented
  illusion of knowing that comes from mere familiarity.

### Speaking-first is a feature, not a limitation

Memai is voice-only, and for language learning that is exactly right. The **production
effect** and Swain's **Output Hypothesis** agree: producing language cements it far
better than recognising it. The tutor prompts you to *speak before it corrects*
(elicit-self-correction-then-recast — prompted output beats passive correction for
uptake), interleaves item types within a session to avoid the blocking trap, and builds
minimal-pair drills for the sound contrasts your ear needs most.

### Anchored to *your* life

The tutor's quiet superpower is the memory engine underneath it: it can pair the
vocabulary you're learning with **episodes from your own life** that Memai already knows
from everyday conversations — the *self-reference effect*, one of the most reliable
memory boosts on record. "How would you tell me about that dinner in Lisbon — in
Italian?" beats any textbook's Mario-orders-a-coffee.

And it is carefully one-directional: lessons draw *from* your personal memory, but never
write to it. Drills and role-play stories are not real events; the tutor never turns a
practice conversation into a fake biography entry, and your life story stays in **your**
language — months of Italian tutoring will not slowly rewrite your episodic memory in
Italian.

### A curriculum you can trust

Course content comes from **curated CEFR-aligned bundles** (the first:
`italian-a0-starter`), not from runtime LLM improvisation — authored definitions stay
authoritative, and one conversation's phrasing never drifts a curated definition. On top
of that backbone, offline enrichment watches for the themes *you* keep bringing up and
proposes the surrounding vocabulary cluster — the curriculum bends toward your
interests without losing its spine.

---

## More to come

The tutor is the proof that the persona machinery works — scoped memory, strategy ports,
voice casts, and bundles are all generic. Natural next specialists share the same shape:
a study partner working through a textbook, an exam-prep coach you delete after the
exam, a domain expert seeded from your own notes. If it benefits from knowing what you
know — and forgetting nothing — it fits.

Want to build one? Start with [Authoring bundles](AUTHORING_BUNDLES.md); the strategy
ports are documented in the codebase (`server/src/memai_server/services/ports.py`), with
the language tutor as the worked example.
