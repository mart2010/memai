# memai-server

The Memai server — the GPU half of [Memai](../README.md). It runs the whole voice
pipeline (faster-whisper STT → Ollama LLM stream → Kokoro TTS) and owns the long-term
memory: PostgreSQL + pgvector storage, offline consolidation, persona strategies, and
bundle installation.

## Install

Follow the [installation guide](../docs/INSTALLATION.md) — it covers system packages
(PostgreSQL + pgvector, Ollama, espeak-ng), the GPU-optional compute path, and the
interactive `memai-setup` wizard that downloads models and writes the config. Native
Windows isn't supported yet for this package — see the guide's "Known limitation"
section.

```bash
cd server
uv sync
.venv/bin/memai-server
```

## Configuration

One bootstrap file: `memai.toml` in the platform config directory — see
[config/memai.example.toml](config/memai.example.toml). It holds only what is needed
before the database exists (ports, DSN, model paths/devices); every other setting lives
in the database and is configured by voice.

## Development

```bash
uv run pytest tests/unit   # unit tests — no GPU or database needed
uv run pytest              # full suite — integration tests need real Postgres + models
```

The package follows Clean Architecture (`domain/` → `services/` → `infrastructure/`);
see [CONTRIBUTING.md](../CONTRIBUTING.md) for conventions and the root `CLAUDE.md` for
the architectural invariants (live/offline boundary, persona scoping).
