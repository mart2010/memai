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

## 5. Set up the PostgreSQL role and database

### Docker setup (recommended)

If you followed the Docker path in step 2, the `memai` database and user were created
automatically by the `POSTGRES_*` environment variables — nothing further to do here,
continue to the next step.

### System apt setup

If you used the system apt path, create the database and role manually. The setup
wizard (next step) collects and verifies the connection for you interactively — this
section documents what it expects to already exist.

```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE memai;
CREATE USER memai;
GRANT ALL PRIVILEGES ON DATABASE memai TO memai;
\c memai
GRANT ALL ON SCHEMA public TO memai;
SQL
```

Note this creates the `memai` role with **no password** — see below for why.

#### Recommended: peer authentication (Linux/macOS, no password stored anywhere)

Since Postgres and the server process always run on the same machine (split-host only
separates *client* from *server* — the database always lives with the server), there's no
need to store a password at all. Postgres can instead trust the OS-authenticated identity
of whoever connects over the local Unix socket (`peer` auth).

Most distributions (confirmed on Ubuntu's PGDG packages) already ship
`local all all peer` in `pg_hba.conf` by default — but bare `peer` auth requires the
connecting OS username to exactly match the Postgres role name, and `memai` is a fixed
role name (not tied to whichever OS user happens to run the server). So map your OS user
to the `memai` role explicitly:

1. Add to `pg_ident.conf` (find its path via `sudo -u postgres psql -c "SHOW ident_file;"`):
   ```
   # MAPNAME    SYSTEM-USERNAME    PG-USERNAME
   memai_map    <your-os-username>    memai
   ```
2. Add to `pg_hba.conf`, **above** the general `local all all peer` line (first match wins):
   ```
   local   memai   memai   peer map=memai_map
   ```
3. Reload Postgres: `sudo systemctl reload postgresql`
4. Connection string: `postgresql:///memai?user=memai` — empty host tells libpq to use the
   default local Unix socket instead of TCP.

The setup wizard offers this as the default option and will print these exact instructions
(with your actual OS username filled in) if the mapping isn't there yet.

**Windows**: `peer` authentication doesn't exist on Windows at all (confirmed against
PostgreSQL's own docs). Its equivalent is `sspi` authentication, which also avoids storing
a password for local connections but is configured differently (`pg_ident.conf` mapping +
`sspi` method instead of `peer`). Not yet supported by the wizard — `server/` is
currently developed on Ubuntu only (see CLAUDE.md) — but worth revisiting if Windows
becomes a real server target.

#### Alternative: password authentication (remote Postgres, or if you'd rather not set up peer auth)

```bash
sudo -u postgres psql -c "ALTER USER memai WITH PASSWORD 'changeme';"
```

Replace `changeme` with a password of your choice — the setup wizard (next step) will
prompt for host/port/user/password and build the connection string for you.
This is the only option for a Postgres instance on a different machine (peer auth is
inherently local-only).

---

## 6. Run the setup wizard

```bash
cd memai/setup
uv sync
.venv/bin/memai-setup
```

`memai-setup` is a separate package from `server/` (its own venv), interactive, and
safe to re-run — it detects an existing install and lets you revisit individual choices
without repeating everything. It walks through:

- **Topology** — single-host (this guide) or split-host.
- **Database connection** — verifies the Postgres role/database from step 5 (peer auth
  or host+password).
- **Prerequisite checks** — Ollama reachable, Postgres/pgvector OK.
- **Compute device** — detects a CUDA GPU, or falls back to CPU for STT/TTS (Ollama
  detects and uses GPU acceleration for the LLM on its own, independent of this check).
- **LLM selection** — lists Ollama models with VRAM-fit hints and pulls the one you
  choose (replaces the old manual `ollama pull aya-expanse` step).
- **Languages** — pick every language you want supported now (main plus any secondary
  ones); which one is *primary* is chosen live during your first conversation.
- **STT model** — picks and pre-downloads a Whisper model size.
- **TTS voices** — downloads a voice per selected language.
- **Embedding model** — pre-downloads `multilingual-e5-large` (used for memory
  consolidation).
- **Config file** — writes `~/.config/memai/memai.toml` (0600 permissions), shared by
  server and client on a single-host setup.
- **Database schema** — applies `migrations/001_initial_schema.sql` (idempotent, safe
  to re-run).
- **Health checks** — confirms Ollama is reachable.

If a download step fails (network hiccup, SSL error), the wizard reports it and moves
on rather than aborting — server startup just downloads the missing piece lazily
instead, which is slower on first launch. Re-run `memai-setup` afterwards to retry the
pre-download.

`memai-server` bootstraps the single `User` row itself on first connect — no manual
`INSERT` needed.

---

## 7. Start the server

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

If any pre-download was skipped or failed during the wizard, Kokoro/Whisper/embedding
weights download now instead (~4 GB total); subsequent starts are fast.

---

## 8. Configure SSH access for the client

Split-host only — if client and server run on the same machine, skip this section (see
`scripts/run-local.sh` to launch both together).

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

**Set `ssh_host` in the client config** (`~/.config/memai/memai.toml` on Linux/macOS,
`%LOCALAPPDATA%\memai\memai.toml` on Windows — see `client/config/memai.example.toml`):

```toml
[server]
ssh_host = "<user>@<server-address>"
```

The tunnel forwards `localhost:8765` on the client to `<server>:8765` — no firewall
rule for port 8765 is needed on the server; SSH (port 22) is sufficient.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `manifest unknown` on `docker pull` | Wrong image tag | Use `pgvector/pgvector:pg16` (no `-ubuntu` suffix) |
| `ollama: connection refused` | Ollama not running | `systemctl start ollama` |
| `CUDA out of memory` | VRAM exhausted | Reduce `stt.compute_type` to `int8` in `memai.toml`; or pick a smaller LLM that fits alongside Whisper+Kokoro |
| No response heard, but no error logged | LLM model too large for VRAM, split across CPU/GPU and evicted when idle — cold reload stalls the next turn | Check `ollama ps` shows `100% GPU`; switch to a smaller model (e.g. `aya-expanse`) |
| Assistant seems to ramble or speaks oddly before answering | Reasoning model (e.g. `qwen3`) emitting `<think>` blocks that get spoken aloud | Use a non-reasoning model; `think: false` does not suppress this on thinking-tuned models |
| Kokoro TTS silent / error | `espeak-ng` missing | `sudo apt install espeak-ng` |
| WebSocket connection refused | Wrong port or server not started | Verify `server.ws_port` matches in both server and client `memai.toml` |
| SSH tunnel fails | No key auth | Run `ssh-copy-id` (step 8) |

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
