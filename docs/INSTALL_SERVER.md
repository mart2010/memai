# Server Installation Guide

The Memai server runs on a GPU-equipped Linux machine (Ubuntu 22.04+ recommended).
It handles speech-to-text, LLM inference, and text-to-speech — all locally, no cloud.

---

## Requirements

| Component | Minimum |
|---|---|
| OS | Ubuntu 22.04+ (other Debian-based distros should work) |
| GPU | NVIDIA GPU with ≥ 8 GB VRAM (10 GB+ recommended for Whisper + Kokoro + LLM simultaneously) |
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

# espeak-ng — phonemiser backend used by Kokoro TTS for non-English languages
sudo apt install -y espeak-ng

# Build tools (needed by some Python wheels)
sudo apt install -y build-essential
```

### PostgreSQL + pgvector

**Option A — Docker (recommended)**

```bash
sudo docker run -d \
  --name memai-postgres \
  --restart unless-stopped \
  -e POSTGRES_USER=memai \
  -e POSTGRES_PASSWORD=memai \
  -e POSTGRES_DB=memai \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

Verify pgvector is available:

```bash
sudo docker exec -it memai-postgres psql -U memai -d memai \
  -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

You should see a version number (e.g. `0.8.2`). The container restarts automatically on
reboot (`--restart unless-stopped`). To persist data across `docker rm`, add
`-v /opt/memai/pgdata:/var/lib/postgresql/data` to the `docker run` command.

**Option B — system apt**

If Docker is not available, add the official PostgreSQL apt repository first, then install:

```bash
sudo apt install -y curl ca-certificates
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail \
  https://www.postgresql.org/media/keys/ACCC4CF8.asc
sudo sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
  https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  > /etc/apt/sources.list.d/pgdg.list'
sudo apt update
sudo apt install -y postgresql-16 postgresql-16-pgvector
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
ollama pull aya-expanse
```

This downloads approximately 5 GB. Ollama must be running (`systemctl status ollama`)
before the server starts — the `install.sh` script above configures it as a systemd
service automatically.

**Model choice matters more than it looks.** Whisper and Kokoro already occupy several
GB of VRAM, so the LLM needs to fit in what's left to run fully on GPU:

- **Avoid large (~70B-class) models like `llama3.3`.** On a 24 GB GPU it doesn't fit
  alongside STT/TTS, so Ollama silently splits it across CPU+GPU — inference becomes
  several times slower, and the model gets evicted from memory after a few idle minutes,
  causing a 30s+ "cold load" stall on the next turn (this is exactly what produced
  apparent silence/no-response on the client during testing).
- **Avoid reasoning models like `qwen3`.** They emit a `<think>...</think>` block before
  the actual answer. `think: false` in the Ollama API does not suppress this for
  thinking-tuned models — the assistant ends up trying to speak its internal reasoning
  out loud, and latency suffers from the extra reasoning tokens regardless.
- **`aya-expanse` (~8B) is a good default**: multilingual by design (useful since Memai
  is language-agnostic), no reasoning overhead, and comfortably fits in VRAM alongside
  Whisper + Kokoro on an 8-10 GB+ GPU.

A good sanity check after pulling any model: `ollama ps` after a chat call should show
`100% GPU` in the `PROCESSOR` column, not a CPU/GPU split.

---

## 6. (Optional) Pre-download AI models

Whisper, Kokoro, and the embedding model are downloaded automatically on first start
(~4 GB total). Run the commands below now to avoid the wait, or skip straight to step 7.

All commands run from `memai/server/` after `uv sync`.

The `hf` CLI is installed automatically as part of `uv sync`. If for any reason it is
missing, install it manually:

```bash
uv pip install huggingface_hub[cli]
```

If downloads fail with an SSL certificate error, prefix commands with `SSL_CERT_FILE`:

```bash
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt uv run hf download ...
```

### Whisper — faster-whisper medium (~1.5 GB)

```bash
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
uv run hf download Systran/faster-whisper-medium \
  --local-dir ~/models/faster-whisper-medium
```

Then point `WHISPER_MODEL_PATH` in your `.env` at the same directory (step 8 sets this up).

Alternatively, set `WHISPER_MODEL_PATH=medium` in `.env` and skip this download entirely —
faster-whisper accepts model names and will download on first start automatically.

### Kokoro voice model (~0.4 GB)

```bash
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
uv run python -c "from kokoro import KPipeline; KPipeline(lang_code='a')"
```

Kokoro manages its own internal download and cache (`~/.cache/huggingface/`); there is no
standalone CLI equivalent. No extra config needed after this runs.

### Embedding model — multilingual-e5-large (~2 GB)

```bash
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
uv run hf download intfloat/multilingual-e5-large
```

Cached to `~/.cache/huggingface/`. No extra config needed.

All three models are public — no HuggingFace account or token is required.

---

## 7. Set up PostgreSQL

### Docker setup (recommended)

If you followed the Docker path in step 2, the `memai` database and user were created
automatically by the `POSTGRES_*` environment variables. Skip straight to the "Run the schema migration"
below — then continue with the User record seed.

### System apt setup

If you used the system apt path, create the database and user manually:

```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE memai;
CREATE USER memai WITH PASSWORD 'changeme';
GRANT ALL PRIVILEGES ON DATABASE memai TO memai;
\c memai
GRANT ALL ON SCHEMA public TO memai;
SQL
```

Replace `changeme` with a password of your choice and update `DATABASE_URL` in step 8.

### Run the schema migration

From the `memai/server` directory.

**Docker:**

```bash
sudo docker exec -i memai-postgres psql -U memai -d memai \
  < migrations/001_initial_schema.sql
```

**System apt:**

```bash
psql -h localhost -U memai -d memai -f migrations/001_initial_schema.sql
```

This creates all tables, HNSW vector indexes, and seeds the `GeneralAssistant` persona.

### Seed the User record

Memai is a single-user application; one row in `users` must exist before the server
can start. Language is left NULL here — it is set during the first voice session via
the onboarding flow.

**Docker:**

```bash
sudo docker exec -it memai-postgres psql -U memai -d memai \
  -c "INSERT INTO users (id, primary_language, secondary_languages) VALUES (gen_random_uuid(), NULL, '{}');"
```

**System apt:**

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
LLM_MODEL=aya-expanse
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
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt .venv/bin/memai-server
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
| `manifest unknown` on `docker pull` | Wrong image tag | Use `pgvector/pgvector:pg16` (no `-ubuntu` suffix) |
| `RuntimeError: No user record found` | User row not inserted | Re-run step 7 seed SQL |
| `ollama: connection refused` | Ollama not running | `systemctl start ollama` |
| `CUDA out of memory` | VRAM exhausted | Reduce `WHISPER_COMPUTE_TYPE` to `int8`; or pick a smaller LLM that fits alongside Whisper+Kokoro |
| No response heard, but no error logged | LLM model too large for VRAM, split across CPU/GPU and evicted when idle — cold reload stalls the next turn | Check `ollama ps` shows `100% GPU`; switch to a smaller model (e.g. `aya-expanse`) |
| Assistant seems to ramble or speaks oddly before answering | Reasoning model (e.g. `qwen3`) emitting `<think>` blocks that get spoken aloud | Use a non-reasoning model; `think: false` does not suppress this on thinking-tuned models |
| Kokoro TTS silent / error | `espeak-ng` missing | `sudo apt install espeak-ng` |
| WebSocket connection refused | Wrong port or server not started | Verify `WS_PORT` matches on both sides |
| SSH tunnel fails | No key auth | Run `ssh-copy-id` (step 9) |

---

## Disk usage summary

| Item | Size (approx.) |
|---|---|
| aya-expanse (Ollama) | ~5 GB |
| Whisper medium | ~1.5 GB |
| multilingual-e5-large | ~2 GB |
| Kokoro voice model | ~0.4 GB |
| Python venv + libs | ~2 GB |
| **Total** | **~11 GB** |
