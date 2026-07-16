# Memai

**AI should make you sharper, not stupid!**

Memai is a voice assistant built to help you actually grow — real expertise, real
knowledge, built through natural conversation, away from a keyboard — instead of quietly
doing your thinking for you. And it does that without asking you to hand your
conversations, your knowledge, or your personal life over to a large AI company to
get there: by default, Memai runs entirely on your own hardware, using open-source
models, so growing your knowledge with it never means giving it away.

No local GPU capable of fast inference? Memai still runs — live conversation can
optionally use a remote LLM provider instead, an explicit choice you make, not a hidden
default. See [Local by default](#local-by-default) below.

---

## What it does

Memai is a real-time voice assistant that listens, thinks, and speaks — and actually
gets to know you over time.

- **Talk naturally.** Speak into your microphone; Memai transcribes, reasons, and
  responds with synthesised speech in under a second.
- **Remembers what matters.** After each session, Memai consolidates your conversations
  into structured long-term memory — the events of your life, the knowledge you've
  explored, the know-how you've picked up — and recalls it when it's relevant.
- **Grows with you.** Engagement levels track how deeply you know each subject, from
  first mention to full integration. A fresh memory brief grounds the assistant in your
  personal context at every session start.
- **Becomes whoever you need.** Switch between specialised [**personas**](docs/PERSONAS.md)
  by voice — each with its own knowledge scope, its own voice cast, and its own way of
  working with you. The first specialist to ship: a research-grounded
  [**language tutor**](docs/PERSONAS.md#the-language-tutor).
- **Speaks your language.** Seven languages fully supported end-to-end: English, French,
  Spanish, Italian, Portuguese, Japanese, and Mandarin Chinese. (Speech recognition
  understands ~99 languages; the local TTS voices are the limiting factor, and the
  supported set grows as they do.)

---

## Built on how human memory actually works

Memai's memory architecture is modelled on decades of cognitive science research. The
goal is not to simulate a brain — it is to make an assistant that *behaves* like one:
remembering what matters, forgetting what does not, and deepening its understanding of
you over time.

### Short-term memory — the working context

Human short-term memory is a limited-capacity workspace that holds only what is
immediately relevant. The LLM context window is its computational analogue — bounded,
precious, and actively managed:

- A **memory brief** (distilled persona and recurring themes) is injected at every session start
- A **session tail** carries the most recent turns from the previous session when continuing a conversation
- A **rolling summary** folds the oldest turns into compact form as sessions grow long, preventing context overflow
- **On-demand recall** pulls targeted chunks from long-term memory directly into the working context when needed

### Long-term memory — three distinct subsystems

Cognitive research distinguishes three types of long-term memory, each with different
structure and retrieval characteristics. Memai implements all three:

| Human memory type | What it stores | Memai equivalent |
|---|---|---|
| **Episodic** | Personal events anchored in time | `Episode` — *"you mentioned the Paris trip last spring"* |
| **Semantic** | Conceptual knowledge about the world | `Concept` — domain knowledge, persona-scoped, synthesised over time |
| **Procedural** | How to do things | `Procedure` — step-by-step or heuristic know-how |

All three types are stored as 1024-dimensional vector embeddings alongside their
structured fields, enabling semantic similarity search at recall time.

### Memory consolidation — the feedback loop

In humans, memories are consolidated during sleep: the hippocampus replays recent
experiences and integrates them into long-term cortical storage. Memai mirrors this with
an **offline consolidation pass** triggered after each session ends:

1. Raw conversation turns are fed to an LLM, which extracts candidate Episodes, Concepts, and Procedures
2. Each candidate is embedded and compared against existing long-term memory via vector similarity — merging with known memories above the threshold, or inserting as new ones below it
3. A fresh memory brief is generated, ready for the next session

This boundary between live conversation (read-only, low-latency) and offline
consolidation (write-heavy, async) is a hard architectural invariant — keeping the
real-time voice loop fast while ensuring nothing is ever lost.

### Engagement levels — depth of learning

Inspired by learning science (spaced repetition, the Ebbinghaus forgetting curve), Memai
tracks how deeply each concept has been absorbed across sessions:

`unseen → mentioned → explored → integrated`

A specialised persona uses this to calibrate responses to your actual level —
introducing a concept gently the first time, and going deep once it is integrated.
Concepts installed from a curated content bundle start as `unseen`; the progression
unfolds naturally through conversation.

---

## Personas — one assistant, many specialists

A persona is more than a system prompt: it is a specialist with its **own scoped
long-term memory**, its own voice cast, and — for advanced personas — its own selection,
assessment, and enrichment strategies plugged into the memory engine. "Big bang" means
one thing to an astronomy tutor and another to a pop-culture companion; Memai keeps them
apart by design.

The first shipped specialist is a **language tutor** built on second-language-acquisition
research: two teacher voices (a fixed native-language anchor and a rotating
target-language voice for phoneme variety), spaced repetition driven by your actual
retrieval performance, and vocabulary anchored to your own memories.

**[Read the full personas story →](docs/PERSONAS.md)**

---

## Architecture

Memai is a two-component monorepo:

```
Microphone → [VAD] → WebSocket → [STT] → [LLM stream] → [TTS] → WebSocket → Speaker
  (client)                        (server)                                    (client)
```

| Component | Runs on | Role |
|---|---|---|
| `client/` | Your everyday machine (Windows, macOS, Linux) | Captures audio, plays back speech, auto-opens SSH tunnel to server |
| `server/` | Linux or macOS (native Windows not yet supported — see [docs/INSTALLATION.md](docs/INSTALLATION.md)). A GPU speeds things up but isn't required — CPU-only works, just slower | STT → LLM → TTS pipeline, persistent memory, consolidation |

**Every model runs locally by default:**

| Task | Model | Optionally remote? |
|---|---|---|
| Speech-to-text | `faster-whisper` | no |
| Language model (live conversation) | `aya-expanse` via `ollama` (streamed) | **yes** — see [Local by default](#local-by-default) |
| Language model (offline memory pipeline) | `aya-expanse` via `ollama` | no — always local, regardless of the above |
| Text-to-speech | `Kokoro`, GPU-accelerated when available | no |
| Embeddings | `multilingual-e5-large` (1024-dim) | no |
| Vector search | PostgreSQL + `pgvector` (HNSW index) | no |

---

## Getting started

**Requirements:** Python 3.13+, a Linux or macOS machine for the server (a GPU speeds
things up but isn't required), PostgreSQL with pgvector, Ollama.

```bash
# Server (Linux/macOS — see docs/INSTALLATION.md for the full walkthrough)
cd server && uv sync
cd ../setup && uv sync && .venv/bin/memai-setup   # interactive wizard: picks models, writes config, applies DB schema
cd ../server && .venv/bin/memai-server

# Client (your machine — Windows, macOS, or Linux)
uv tool install "git+<repo-url>#subdirectory=client"
memai-client
```

Client and server on the same machine? `./scripts/run-local.sh` (`.\scripts\run-local.ps1`
on Windows) starts the server, waits for it to be ready, then launches the client in the
same terminal — no SSH tunnel needed.

On first launch, Memai guides you through language selection. After that, everything is
configured by voice — no CLI arguments, no config files to edit.

**[Full installation guide →](docs/INSTALLATION.md)**

---

## Local by default

Memai's default install is fully local and air-gapped-capable — this is the recommended
setup, and the one the install wizard configures unless you tell it otherwise:

- **Zero external calls.** No API keys, no telemetry, no model downloads after setup.
- **Your data, your disk.** Conversations are written to local JSONL files; structured
  memory lives in your own PostgreSQL instance.
- **Air-gapped capable.** Once models are downloaded, everything runs with no internet
  access at all.
- **Live/offline boundary.** During a conversation, only flat files are written. Heavy
  processing (DB writes, LLM extraction, embedding generation) happens offline, after the
  session ends — keeping latency low and your conversation data off any database in real
  time.

**Not everyone has a GPU capable of fast local inference, though** — and that shouldn't
be the reason Memai doesn't work for you. Live conversation (only) can instead use a
remote OpenAI-compatible LLM endpoint — OpenRouter, OpenAI, a self-hosted `vLLM`/LM
Studio server, anything speaking that protocol — an explicit choice made once during
setup, not a silent fallback. Everything else is unaffected: your memory, your
embeddings, your speech pipeline, and even the *offline* half of the memory system
(the pass that extracts and consolidates what you've learned) always run on your own
machine via a local Ollama model, regardless of this choice — slower on CPU, never sent
anywhere.

| Task | Local (default) | Optional remote |
|---|---|---|
| Language model — live conversation | `aya-expanse` via Ollama | any OpenAI-compatible endpoint |
| Language model — offline memory pipeline | `aya-expanse` via Ollama | always local, no exceptions |
| Speech-to-text, text-to-speech, embeddings, memory store | local | none ship today — the architecture's ports allow it, but the LLM is the only one actually wired up |

If you do configure a remote endpoint, know what that trades away: the **LLM** sees each
live conversation in full, transiently (Memai never asks it to store anything on its
side). Your **long-term memory** — every Episode, Concept, and Procedure you have ever
built — is untouched by this choice: it is extracted, embedded, and stored entirely
locally, by the always-local offline pipeline described above.

The fully local setup stays the recommended default. The remote option exists so that
not having access to expensive GPU infrastructure doesn't have to mean not having access
to Memai at all.

---

## Documentation

| Page | What it covers |
|---|---|
| [Specification](docs/spec/SPEC.md) | The canonical spec: [glossary](docs/spec/GLOSSARY.md), [functional](docs/spec/FUNCTIONAL.md) and [technical](docs/spec/TECHNICAL.md) requirements |
| [Personas](docs/PERSONAS.md) | The persona concept, the language tutor, and the research behind them |
| [Installation](docs/INSTALLATION.md) | Topologies, requirements, and the step-by-step guide for both server and client, on every supported OS |
| [Authoring bundles](docs/AUTHORING_BUNDLES.md) | How to write curriculum content for a persona |
| [Contributing](CONTRIBUTING.md) | Setup, architecture conventions, and PR guidelines |

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.
See the [LICENSE](LICENSE) file for details.
