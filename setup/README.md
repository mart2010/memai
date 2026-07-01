# memai-setup

Installation wizard for Mémai. Guides a fresh install (single-host or split-host
topology), resolves the LLM/STT/TTS catalogues against detected GPU VRAM and chosen
languages, writes the server/client config files, and runs health checks.

See the root `CLAUDE.md` and `docs/PLAN.md` for architecture and status.

```bash
cd setup
uv sync
uv run memai-setup          # any OS
# .venv/Scripts/memai-setup   # Windows, if you prefer invoking the venv directly
# .venv/bin/memai-setup       # Linux/macOS
```
