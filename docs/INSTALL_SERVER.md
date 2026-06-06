# Server Installation Guide

The Memai server runs on a GPU-equipped Linux machine (Ubuntu 22.04+ recommended).
It handles speech-to-text, LLM inference, and text-to-speech — all locally, no cloud.

---

## Requirements

| Component | Minimum |
|---|---|
| OS | Ubuntu 22.04+ (other Debian-based distros should work) |
| GPU | NVIDIA GPU with ≥ 8 GB VRAM (10 GB+ recommended for Whisper + LLM simultaneously) |
| RAM | 16 GB system RAM |
| Disk | ~20 GB free (models + venv + logs) |
| NVIDIA driver | 525+ (supports CUDA 12.x) |

---

## 1. Verify the NVIDIA driver

```bash
nvidia-smi
```

The output should show your GPU and driver version. If the command is not found, install
the driver for your GPU before continuing — driver installation is hardware-specific and
outside the scope of this guide.

CUDA runtime is bundled inside the Python wheels (CTranslate2, PyTorch) — no separate
CUDA toolkit installation is needed; only the driver is required.

---

## 2. Install system packages

```bash
sudo apt update

# PostgreSQL 15 + pgvector
sudo apt install -y postgresql-15 postgresql-15-pgvector

# espeak-ng — phonemiser backend used by Kokoro TTS for non-English languages
sudo apt install -y espeak-ng

# Build tools (needed by some Python wheels)
sudo apt install -y build-essential
```

### Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Verify it is running:

```bash
ollama list    # should return an empty table on a fresh install
```

---

## 3. Install Python 3.13+ and uv

If Python 3.13 is not available via `apt` on your distro, use the deadsnakes PPA:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install -y python3.13 python3.13-venv
```

Install uv (the project's package manager — do not use pip):

```bash
curl -Lsf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env   # or restart your shell
```

---

## 4. Clone the repository and install the server package

```bash
git clone <repo-url> memai
cd memai/server
uv sync
```

`uv sync` creates `.venv/` and installs all Python dependencies declared in
`pyproject.toml`, including `faster-whisper`, `kokoro`, `sentence-transformers`,
`psycopg`, and `websockets`.

---

## 5. Pull the LLM model

```bash
ollama pull llama3.3
```

This downloads approximately 9 GB. Ollama must be running (`systemctl status ollama`)
before the server starts — the `install.sh` script above configures it as a systemd
service automatically.

---

## 6. (Optional) Pre-download AI models

Whisper, Kokoro, and the embedding model are downloaded automatically on first start
(~4 GB total). Run the commands below now to avoid the wait, or skip straight to step 7.

All commands run from `memai/server/` after `uv sync`.

### Whisper — faster-whisper medium (~1.5 GB)

```bash
uv run python -c "
from huggingface_hub import snapshot_download
snapshot_download('Systran/faster-whisper-medium', local_dir='$HOME/models/faster-whisper-medium')
"
```

Then point `WHISPER_MODEL_PATH` in your `.env` at the same directory (step 8 sets this up).

### Kokoro voice model (~0.4 GB)

```bash
uv run python -c "from kokoro import KPipeline; KPipeline(lang_code='a')"
```

This downloads the shared Kokoro weights to `~/.cache/huggingface/`. No extra config needed.

### Embedding model — multilingual-e5-large (~2 GB)

```bash
uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-large')"
```

Cached to `~/.cache/huggingface/`. No extra config needed.

---

## 7. Set up PostgreSQL

### Create the database and a dedicated user

```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE memai;
CREATE USER memai WITH PASSWORD 'changeme';
GRANT ALL PRIVILEGES ON DATABASE memai TO memai;
\c memai
GRANT ALL ON SCHEMA public TO memai;
SQL
```

Replace `changeme` with a password of your choice.

### Run the schema migration

From the `memai/server` directory:

```bash
psql -h localhost -U memai -d memai -f migrations/001_initial_schema.sql
```

This creates all tables, HNSW vector indexes, and seeds the `GeneralAssistant` persona.

### Seed the User record

Memai is a single-user application; one row in `users` must exist before the server
can start. Language is left NULL here — it is set during the first voice session via
the onboarding flow.

```bash
psql -h localhost -U memai -d memai <<'SQL'
INSERT INTO users (id, primary_language, secondary_languages)
VALUES (gen_random_uuid(), NULL, '{}');
SQL
```

---

## 8. Configure environment variables

Create a `.env` file in `memai/server/` (or set these variables in your shell / systemd
unit — whichever you prefer):

```dotenv
# ── WebSocket ──────────────────────────────────────────────────────────────
WS_PORT=8765

# ── STT (faster-whisper) ───────────────────────────────────────────────────
# Path to a pre-downloaded faster-whisper model directory, or a model name
# that faster-whisper will download on first run (e.g. "small", "medium").
WHISPER_MODEL_PATH=~/models/faster-whisper-medium
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16   # use int8 if VRAM is tight

# ── LLM (Ollama) ───────────────────────────────────────────────────────────
LLM_MODEL=llama3.3
# OLLAMA_HOST=http://localhost:11434   # uncomment if Ollama runs on another host

# ── Database (PostgreSQL) ──────────────────────────────────────────────────
# Standard libpq DSN; adjust host/port/password as needed.
DATABASE_URL=postgresql://memai:changeme@localhost:5432/memai

# ── Session logs ───────────────────────────────────────────────────────────
LOG_DIR=logs/sessions

# ── Onboarding ─────────────────────────────────────────────────────────────
# Set PRIMARY_LANGUAGE to skip the language-selection step on first connect.
# Valid values: en, fr, es, it, pt, ja, ko, zh-cn
# Leave unset (default) to let the server prompt the client to choose.
# PRIMARY_LANGUAGE=en
```

---

## 9. Start the server

```bash
cd memai/server
.venv/bin/memai-server
```

Expected startup output:

```
Loading Whisper model…
Services ready.
Server listening on :8765
```

If you skipped step 6, Kokoro and Whisper will download their weights now (~4 GB total);
subsequent starts are fast.

---

## 10. Configure SSH access for the client

The client auto-establishes an SSH tunnel to the server before opening the WebSocket.
Key-based authentication must be in place:

**On the client machine:**

```bash
ssh-keygen -t ed25519 -C "memai-client"
```

**Copy the public key to the server:**

```bash
ssh-copy-id <user>@<server-address>
```

**Set `SSH_USER_HOST` on the client** (see `client/.env`):

```dotenv
SSH_USER_HOST=<user>@<server-address>
```

The tunnel forwards `localhost:8765` on the client to `<server>:8765` — no firewall
rule for port 8765 is needed on the server; SSH (port 22) is sufficient.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: No user record found` | User row not inserted | Re-run step 6 seed SQL |
| `ollama: connection refused` | Ollama not running | `systemctl start ollama` |
| `CUDA out of memory` | VRAM exhausted | Reduce `WHISPER_COMPUTE_TYPE` to `int8`; or offload LLM with `OLLAMA_NUM_GPU=0` |
| Kokoro TTS silent / error | `espeak-ng` missing | `sudo apt install espeak-ng` |
| WebSocket connection refused | Wrong port or server not started | Verify `WS_PORT` matches on both sides |
| SSH tunnel fails | No key auth | Run `ssh-copy-id` (step 9) |

---

## Disk usage summary

| Item | Size (approx.) |
|---|---|
| llama3.3 (Ollama) | ~9 GB |
| Whisper medium | ~1.5 GB |
| multilingual-e5-large | ~2 GB |
| Kokoro voice model | ~0.4 GB |
| Python venv + libs | ~2 GB |
| **Total** | **~15 GB** |
