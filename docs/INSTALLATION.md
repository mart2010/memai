# Installing Memai

Memai has two runtime components plus an interactive setup wizard:

| Component | Runs on | Role |
|---|---|---|
| **Server** (`server/`) | Linux or macOS; a GPU speeds things up but isn't required | Speech-to-text, LLM, text-to-speech, long-term memory |
| **Client** (`client/`) | Your everyday machine — Windows, macOS, or Linux | Microphone capture, audio playback |
| **Setup wizard** (`setup/`) | The server machine | One-time interactive install: picks models against your hardware, downloads everything, writes config, applies the DB schema |

This is the one guide for every combination of topology and OS below — follow the steps
in order, using the column/branch for your platform where they differ. Everything past
"run the wizard" is identical regardless of OS or topology.

---

## 1. Choose your topology

**Single-host** — client and server run on the same machine. Both read the same
`memai.toml`, and a `run-local` convenience script starts the server and launches the
client in one go. No SSH involved.

**Split-host** — the server lives on a separate (typically GPU) machine, the client on
your everyday laptop. The client automatically opens an SSH tunnel to the server; no
ports are exposed beyond SSH.

## 2. Check requirements

| Requirement | Server | Client |
|---|---|---|
| Python 3.13+ and [uv](https://docs.astral.sh/uv/) | yes | yes |
| GPU | optional — NVIDIA (CUDA 12, driver 525+) accelerates STT/TTS/LLM; no GPU falls back to CPU for STT/TTS (fully functional, just slower) and CPU-only Ollama for the LLM | no |
| PostgreSQL 16 + pgvector | yes | no |
| Ollama | yes | no |
| Disk for models | ~11 GB (see [disk usage](#disk-usage-summary)) | none |
| SSH access to the server | — | split-host only |

**OS support today:**

| OS | Server | Client |
|---|---|---|
| Linux | yes — primary development/verification platform | yes |
| macOS | yes, in principle (no known blocker) — not yet live-verified | yes |
| Windows | **not yet** — see [Known limitation: native Windows server](#known-limitation-native-windows-server) | yes — this is the actively-used dev client platform |

No CUDA toolkit install is needed on any OS (the Python wheels bundle the runtime), and
after the initial model downloads the whole system runs without internet access.

---

## 3. Install Python 3.13+ and uv

**Linux / macOS:**

```bash
curl -Lsf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env   # or restart your shell
```

If Python 3.13 isn't available via your distro's package manager, use the
[deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa) on
Debian/Ubuntu (`sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install -y
python3.13 python3.13-venv`), or Homebrew (`brew install python@3.13`) on macOS.

**Windows** (client only, until the limitation above is resolved):

Download and install Python 3.13+ from [python.org](https://www.python.org/downloads/),
then:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## 4. Install system packages

Only install what your role/OS needs — server and client have almost no overlap.

### Server: PostgreSQL + pgvector

**Option A — Docker (recommended, works the same on Linux/macOS)**

```bash
docker run -d \
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
docker exec -it memai-postgres psql -U memai -d memai \
  -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

You should see a version number (e.g. `0.8.2`). To persist data across `docker rm`, add
`-v /opt/memai/pgdata:/var/lib/postgresql/data` to the `docker run` command.

**Option B — native package (Linux only)**

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

Then create the role/database (the setup wizard verifies the connection interactively
in a later step — this just needs to exist):

```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE memai;
CREATE USER memai;
GRANT ALL PRIVILEGES ON DATABASE memai TO memai;
\c memai
GRANT ALL ON SCHEMA public TO memai;
SQL
```

This creates the `memai` role with **no password**, on purpose — see below.

<details>
<summary>Recommended: peer authentication (Linux/macOS, no password stored anywhere)</summary>

Since Postgres and the server process always run on the same machine (split-host only
separates *client* from *server* — the database always lives with the server), there's
no need to store a password at all. Postgres can trust the OS-authenticated identity of
whoever connects over the local Unix socket (`peer` auth).

Most distributions (confirmed on Ubuntu's PGDG packages) already ship
`local all all peer` in `pg_hba.conf` by default — but bare `peer` auth requires the
connecting OS username to exactly match the Postgres role name, and `memai` is a fixed
role name. So map your OS user to the `memai` role explicitly:

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
4. Connection string: `postgresql:///memai?user=memai` — empty host tells libpq to use
   the default local Unix socket instead of TCP.

The setup wizard offers this as the default option and will print these exact
instructions (with your actual OS username filled in) if the mapping isn't there yet.
</details>

<details>
<summary>Alternative: password authentication (remote Postgres, Docker, or if you'd rather not set up peer auth)</summary>

```bash
sudo -u postgres psql -c "ALTER USER memai WITH PASSWORD 'changeme';"
```

Replace `changeme` with a password of your choice — the setup wizard prompts for
host/port/user/password and builds the connection string for you. This is the only
option for a Postgres instance on a different machine (peer auth is inherently
local-only), and the Docker path above already sets a password this way.
</details>

### Server: Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama list    # should return an empty table on a fresh install
```

(macOS also has a native [Ollama.app](https://ollama.com/download/mac) if you prefer a
GUI installer over the shell script.)

### Server: espeak-ng and build tools (Linux)

```bash
sudo apt update
sudo apt install -y espeak-ng build-essential
```

`espeak-ng` is the phonemiser backend Kokoro TTS uses for non-English languages.
`build-essential` is needed by some Python wheels during `uv sync`.

On macOS: `brew install espeak-ng`.

### Client: PortAudio (macOS/Linux only)

```bash
# macOS
brew install portaudio

# Linux
sudo apt install -y libportaudio2
```

Windows client wheels already bundle PortAudio — nothing to install.

### Client: SSH (split-host only)

Windows 10+, macOS, and every mainstream Linux distro ship an SSH client already.
Verify with `ssh -V`.

---

## 5. Clone the repo and install the packages

**Server** (Linux/macOS — see the [Windows limitation](#known-limitation-native-windows-server) below):

```bash
git clone <repo-url> memai
cd memai/server
uv sync
```

`uv sync` creates `.venv/` and installs every server dependency (`faster-whisper`,
`kokoro`, `sentence-transformers`, `psycopg`, `websockets`, …).

**Single-host** also needs the client package built from the same clone:

```bash
cd ../client
uv sync
```

**Client-only machine** (split-host, or any platform) — no need to clone the whole repo,
install it directly as an isolated tool:

```bash
uv tool install "git+<repo-url>#subdirectory=client"
```

This puts `memai-client` on your PATH in its own managed environment.

---

## 6. Split-host only: SSH key authentication

Skip this section for single-host.

The client opens an SSH tunnel to the server automatically on startup. Key-based auth
avoids a password prompt on every connect.

**On the client machine, generate a key** (skip if you already have one):

```bash
ssh-keygen -t ed25519 -C "memai-client"
```

**Copy the public key to the server:**

```bash
ssh-copy-id <user>@<server-address>
```

On Windows, if `ssh-copy-id` isn't available:

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh <user>@<server-address> "cat >> ~/.ssh/authorized_keys"
```

**Verify it works without a password:**

```bash
ssh <user>@<server-address> echo ok
```

**Set `ssh_host`** in the client's config (written in the next step, or by hand —
`~/.config/memai/memai.toml` on Linux/macOS, `%LOCALAPPDATA%\memai\memai.toml` on
Windows):

```toml
[server]
ssh_host = "<user>@<server-address>"
```

The tunnel forwards `localhost:8765` on the client to `<server>:8765` — no firewall
rule for port 8765 is needed on the server; SSH (port 22) is sufficient.

---

## 7. Run the setup wizard (on the server machine)

```bash
cd memai/setup
uv sync
.venv/bin/memai-setup
```

`memai-setup` is a separate package (its own venv), interactive, and safe to re-run — it
detects an existing install and pre-fills your previous choices rather than starting
over. It walks through, in order:

- **Topology** — single-host or split-host (matching what you picked in step 1).
- **Database connection** — verifies the Postgres role/database from step 4 (peer auth
  on Linux/macOS, or host+password).
- **Prerequisite checks** — Ollama reachable, Postgres/pgvector OK.
- **Compute device** — detects a CUDA GPU, or falls back to CPU for STT/TTS (Ollama
  detects and uses GPU acceleration for the LLM on its own, independent of this check).
  On Linux, a non-NVIDIA GPU (e.g. an AMD Ryzen AI APU) is separately identified by name
  where possible, so the message is accurate about what's actually in the machine instead
  of just reporting "no GPU" — STT/TTS still run on CPU either way (no ROCm-accelerated
  adapter exists yet).
- **LLM selection** — lists Ollama models with VRAM-fit hints; on a non-NVIDIA GPU, uses
  its identified memory for the same fit hints where available, since Ollama can actually
  place the LLM on it (confirmed on a real AMD Ryzen AI APU box) — unlike STT/TTS.
- **Languages** — pick every language you want supported now (main plus any secondary
  ones); which one is *primary* is chosen live during your first conversation.
- **STT model** — picks and pre-downloads a Whisper model size.
- **TTS voices** — downloads a voice per selected language.
- **Embedding model** — pre-downloads `multilingual-e5-large` (used for memory
  consolidation).
- **Config file** — writes `~/.config/memai/memai.toml` (`%LOCALAPPDATA%\memai\` on
  Windows clients), 0600 permissions where supported; shared by server and client on a
  single-host setup.
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

## 8. Start it

**Single-host** — one command starts the server, waits for it to be ready, then
launches the client:

```bash
./scripts/run-local.sh          # Linux/macOS
```

```powershell
.\scripts\run-local.ps1         # Windows client only, against a WSL2/remote server — see limitation below
```

**Split-host** — start each independently.

Server:

```bash
cd memai/server
.venv/bin/memai-server
```

Expected output:

```
Loading Whisper model…
Services ready.
Server listening on :8765
```

If any pre-download was skipped or failed during the wizard, Kokoro/Whisper/embedding
weights download now instead (~4 GB total); subsequent starts are fast.

Client:

```bash
memai-client
```

Expected output:

```
Starting SSH tunnel...
Connected
```

The server must be running before the client starts. On first connect, if no primary
language is configured on the server, a language selection prompt appears in the
terminal — after that, everything is configured by voice.

---

## Known limitation: native Windows server

Running `memai-server` (the STT/LLM/TTS pipeline) directly on Windows is **not
supported yet** — `uv sync` in `server/` fails building `numpy` from source, because
the locked `numpy==1.26.4` predates Python 3.13's Windows wheels and no C/C++ compiler
is present by default on Windows to build it. This is unrelated to having a GPU — it
reproduces on CPU-only setups too. A proper fix means bumping the `spacy`/`thinc`/
`blis` chain that Kokoro's English G2P pulls in (they constrain the resolver to that
old `numpy`) to versions with native Windows/cp313 wheels; not yet done.

**Practical workaround today**: run the server inside **WSL2** (Ubuntu) on the same
Windows machine — this follows the documented Linux steps above unmodified and gets you
a genuine single-host setup on Windows hardware, with the native Windows client talking
to the WSL2 server over `localhost` (modern WSL2 forwards `localhost` ports to Windows
automatically) or over the same SSH-tunnel mechanism as any split-host client if
`localhost` forwarding isn't enabled on your WSL2 version. Not yet live-verified
end-to-end in this repo — treat it as the recommended path, not a guarantee.

The native Windows client (this document's default assumption everywhere else) is
unaffected and already the actively-used development platform for `client/`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `manifest unknown` on `docker pull` | Wrong image tag | Use `pgvector/pgvector:pg16` (no `-ubuntu` suffix) |
| `ollama: connection refused` | Ollama not running | `systemctl start ollama` (Linux) or start the Ollama app (macOS) |
| `CUDA out of memory` | VRAM exhausted | Reduce `stt.compute_type` to `int8` in `memai.toml`; or pick a smaller LLM that fits alongside Whisper+Kokoro |
| No response heard, but no error logged | LLM model too large for VRAM, split across CPU/GPU and evicted when idle — cold reload stalls the next turn | Check `ollama ps` shows `100% GPU`; switch to a smaller model (e.g. `aya-expanse`) |
| Assistant seems to ramble or speaks oddly before answering | Reasoning model (e.g. `qwen3`) emitting `<think>` blocks that get spoken aloud | Use a non-reasoning model; `think: false` does not suppress this on thinking-tuned models |
| Kokoro TTS silent / error | `espeak-ng` missing | Install it (see step 4) |
| `uv sync` fails building `numpy` on Windows | Known limitation, see above | Use WSL2 for the server; the native client is unaffected |
| WebSocket connection refused | Wrong port or server not started | Verify `server.ws_port` matches in both server and client `memai.toml` |
| SSH tunnel fails | No key auth | Run `ssh-copy-id` (step 6) |
| `FileNotFoundError: Client config not found` | `memai.toml` missing | The wizard writes it for single-host; for a client-only machine, create it as shown in step 6 |
| Client connects to `localhost` instead of tunneling | `ssh_host` missing/blank in `memai.toml` | Set `ssh_host` (step 6) |
| No audio input detected | Wrong microphone or permissions | Check OS audio settings; grant mic permission |
| `OSError: PortAudio not found` | Missing PortAudio (macOS/Linux client) | Install via `brew install portaudio` or `apt install libportaudio2` |

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
