# memai-setup

The installation wizard for [Memai](../README.md). Run once on the server machine after
the system prerequisites are in place: it detects your GPU, resolves the LLM/STT/TTS
model catalogues against available VRAM and your chosen languages, downloads the models,
writes the `memai.toml` config file(s) for a single-host or split-host topology, applies
the database schema, and runs health checks.

It is safe to re-run at any time — for example to retry a failed download or to change
models or languages later (model/engine changes are wizard territory, never voice
configuration).

## Run

```bash
cd setup
uv sync
uv run memai-setup
```

See [docs/INSTALLATION.md](../docs/INSTALLATION.md) for where the wizard fits in the
overall install, and [docs/INSTALL_SERVER.md](../docs/INSTALL_SERVER.md) for the
step-by-step server guide (the wizard is its step 6).
