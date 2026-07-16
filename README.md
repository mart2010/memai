# Memai

**Your personal AI voice assistant — 100% private, 100% local, never leaves your home network.**

No cloud. No subscriptions. No data ever sent to a third party. Memai runs entirely on
your own hardware, using open-source models, and keeps every conversation, memory, and
learned insight locked inside your home network.

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
| `server/` | Any NVIDIA GPU-equipped machine (Linux recommended; Windows with CUDA untested) | STT → LLM → TTS pipeline, persistent memory, consolidation |

**All models run locally:**

| Task | Model |
|---|---|
| Speech-to-text | `faster-whisper` |
| Language model | `aya-expanse` via `ollama` (streamed) |
| Text-to-speech | `Kokoro`, CUDA-accelerated |
| Embeddings | `multilingual-e5-large` (1024-dim) |
| Vector search | PostgreSQL + `pgvector` (HNSW index) |

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

## Privacy by design

- **Zero external calls.** No API keys, no telemetry, no model downloads after setup.
- **Your data, your disk.** Conversations are written to local JSONL files; structured memory lives in your PostgreSQL instance.
- **Air-gapped capable.** Once models are downloaded, the system runs with no internet access whatsoever.
- **Live/offline boundary.** During a conversation, only flat files are written. Heavy processing (DB writes, LLM extraction, embedding generation) happens offline, after the session ends — keeping latency low and your conversation data from touching a DB in real time.

---

## Deployment alternatives

The default setup above is fully local and air-gapped. But every inference service in
Memai is a swappable adapter behind a clean port — there is no lock-in. If you are
comfortable trading some privacy for convenience (or simply do not have a GPU), each
component can be replaced independently:

| Component | Local (default) | Cloud alternative |
|---|---|---|
| Speech-to-text | `faster-whisper` on GPU | Whisper API, Deepgram, AssemblyAI |
| Language model | `aya-expanse` via `ollama` | OpenRouter, OpenAI, Anthropic, … |
| Text-to-speech | Kokoro on GPU | ElevenLabs, Azure TTS, … |
| Embeddings | `multilingual-e5-large` on CPU/GPU | OpenAI Embeddings API |
| Memory store | PostgreSQL on your machine | Managed cloud PostgreSQL + pgvector |

An OpenRouter LLM adapter already ships in the codebase as the first cloud
implementation of the LLM port; making it a selectable option in the setup wizard is on
the roadmap. Everything else stays local.

**On the privacy spectrum**, the components are not equally sensitive:

- **LLM** — sees each conversation in full, but only transiently. No conversation text is stored by Memai on the provider's side.
- **STT / TTS** — audio and synthesised speech pass through the provider. If you use a cloud STT, each utterance is sent upstream.
- **Embeddings** — the most sensitive to outsource. Every Episode, Concept, and Procedure you have ever stored gets fingerprinted by the embedding provider if you swap this out. The local default keeps your entire long-term memory index private.
- **Memory store** — the crown jewel. Hosting PostgreSQL on a cloud VM is reasonable (you own the instance); using a fully managed third-party DB-as-a-service means your accumulated personal knowledge lives on someone else's disk.

The fully local setup is the recommended default. Everything else is an explicit
trade-off that you make with full awareness.

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
