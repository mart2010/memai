# Memai

**Your personal AI voice assistant — 100% private, 100% local, never leaves your home network.**

No cloud. No subscriptions. No data ever sent to a third party. Memai runs entirely on your own hardware, using open-source models, and keeps every conversation, memory, and learned insight locked inside your home network.

---

## What it does

Memai is a real-time voice assistant that listens, thinks, and speaks — and actually gets to know you over time.

- **Talk naturally.** Speak into your microphone; Memai transcribes, reasons, and responds with synthesised speech in under a second.
- **Remembers everything that matters.** After each session, Memai consolidates your conversations into structured long-term memory: episodic events, domain knowledge (Concepts), and how-to knowledge (Procedures). It recalls relevant memories when you need them.
- **Grows with you.** Engagement levels track how well you know a subject — from first mention to full integration. Memory briefs keep the assistant grounded in your personal context at every session start.
- **Multiple personas.** Switch between specialised assistants (language tutor, study partner, general assistant…) by voice, each with its own knowledge scope.
- **Speaks your language.** Supports 17 languages out of the box: `en fr es de it pt pl tr ru nl cs ar zh-cn ja ko hu hi`.

---

## Cognitive inspiration

Memai's memory architecture is modelled on how human memory actually works, drawing from decades of cognitive science research and modern AI memory frameworks. The goal is not to simulate a brain — it is to make an assistant that *behaves* like one: remembering what matters, forgetting what does not, and deepening its understanding of you over time.

### Short-term memory — the working context

Human short-term memory (STM) is a limited-capacity workspace that holds only what is immediately relevant. The LLM context window is its computational analogue — bounded, precious, and actively managed:

- A **memory brief** (distilled persona and recurring themes) is injected at every session start
- A **session tail** carries the most recent turns from the previous session when continuing a conversation
- A **rolling summary** folds the oldest turns into compact form as sessions grow long, preventing context overflow
- **On-demand recall** pulls targeted chunks from long-term memory directly into the working context when needed

### Long-term memory — three distinct subsystems

Cognitive research distinguishes three types of long-term memory, each with different structure and retrieval characteristics. Memai implements all three:

| Human memory type | What it stores | Memai equivalent |
|---|---|---|
| **Episodic** | Personal events anchored in time | `Episode` — *"you mentioned the Paris trip last spring"* |
| **Semantic** | Conceptual knowledge about the world | `Concept` — domain knowledge, persona-scoped, synthesised over time |
| **Procedural** | How to do things | `Procedure` — step-by-step or heuristic know-how |

All three types are stored as 1024-dimensional vector embeddings alongside their structured fields, enabling semantic similarity search at recall time.

### Memory consolidation — the feedback loop

In humans, memories are consolidated during sleep: the hippocampus replays recent experiences and integrates them into long-term cortical storage. Memai mirrors this with an **offline consolidation pass** triggered after each session ends:

1. Raw conversation turns are fed to an LLM, which extracts candidate Episodes, Concepts, and Procedures
2. Each candidate is embedded and compared against existing long-term memory via vector similarity — merging with known memories above the threshold, or inserting as new ones below it
3. A fresh memory brief is generated, ready for the next session

This boundary between live conversation (read-only, low-latency) and offline consolidation (write-heavy, async) is a hard architectural invariant — keeping the real-time voice loop fast while ensuring nothing is ever lost.

### Engagement levels — depth of learning

Inspired by learning science (spaced repetition, the Ebbinghaus forgetting curve), Memai tracks how deeply each concept has been absorbed across sessions:

`unseen → mentioned → explored → practiced → integrated`

A specialised persona uses this to calibrate responses to the user's actual level — introducing a concept gently the first time, and going deep once it is integrated. Concepts loaded from injected reference documents start as `unseen`; the progression unfolds naturally through conversation.

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

**Requirements:** Python 3.13+, a GPU server with CUDA, PostgreSQL with pgvector.

```bash
# Server (GPU machine — Linux/Ubuntu)
cd server && uv sync
.venv/bin/memai-server

# Client (your machine — Windows, macOS, or Linux)
uv tool install "git+<repo-url>#subdirectory=client"
# Copy client/config/memai.example.toml to your platform config dir (see docs/INSTALL_CLIENT.md) and set ssh_host
memai-client
```

On first launch, Memai guides you through language selection. After that, everything is configured by voice — no CLI arguments, no config files to edit.

---

## Privacy by design

- **Zero external calls.** No API keys, no telemetry, no model downloads after setup.
- **Your data, your disk.** Conversations are written to local JSONL files; structured memory lives in your PostgreSQL instance.
- **Air-gapped capable.** Once models are downloaded, the system runs with no internet access whatsoever.
- **Live/offline boundary.** During a conversation, only flat files are written. Heavy processing (DB writes, LLM extraction, embedding generation) happens offline, after the session ends — keeping latency low and your conversation data from touching a DB in real time.

---

## Deployment alternatives

The default setup above is fully local and air-gapped. But every inference service in Memai is a swappable adapter — there is no lock-in. If you are comfortable trading some privacy for convenience (or simply do not have a GPU), each component can be replaced independently:

| Component | Local (default) | Cloud alternative |
|---|---|---|
| Speech-to-text | `faster-whisper` on GPU | Whisper API, Deepgram, AssemblyAI |
| Language model | `aya-expanse` via `ollama` | OpenRouter, OpenAI, Anthropic, … |
| Text-to-speech | Kokoro on GPU | ElevenLabs, Azure TTS, … |
| Embeddings | `multilingual-e5-large` on CPU/GPU | OpenAI Embeddings API |
| Memory store | PostgreSQL on your machine | Managed cloud PostgreSQL + pgvector |

OpenRouter support is already built in — set `OPENROUTER_API_KEY` and pick your model. Everything else stays local.

**On the privacy spectrum**, the components are not equally sensitive:

- **LLM** — sees each conversation in full, but only transiently. No conversation text is stored by Memai on the provider's side.
- **STT / TTS** — audio and synthesised speech pass through the provider. If you use a cloud STT, each utterance is sent upstream.
- **Embeddings** — the most sensitive to outsource. Every Episode, Concept, and Procedure you have ever stored gets fingerprinted by the embedding provider if you swap this out. The local default keeps your entire long-term memory index private.
- **Memory store** — the crown jewel. Hosting PostgreSQL on a cloud VM is reasonable (you own the instance); using a fully managed third-party DB-as-a-service means your accumulated personal knowledge lives on someone else's disk.

The fully local setup is the recommended default. Everything else is an explicit trade-off that you make with full awareness.

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, architecture conventions, and PR guidelines.

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. See the [LICENSE](LICENSE) file for details.
