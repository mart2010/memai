# Installing Memai

Memai has two runtime components plus an interactive setup wizard:

| Component | Runs on | Role |
|---|---|---|
| **Server** (`server/`) | Linux, macOS, or Windows; a GPU speeds things up but isn't required | Speech-to-text, LLM, text-to-speech, long-term memory |
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
| GPU | optional — NVIDIA (CUDA 12, driver 525+) accelerates STT/TTS/LLM; no GPU falls back to CPU for STT/TTS (fully functional, just slower) and CPU-only Ollama for the LLM, or to a [remote LLM](#7-run-the-setup-wizard-on-the-server-machine) for the live conversation path | no |
| C/C++ compiler | Windows only — see [step 4](#4-install-system-packages) | no |
| PostgreSQL 16 + pgvector | yes | no |
| Ollama | yes — even with a remote LLM (FR-707), the offline memory pipeline always runs locally | no |
| Disk for models | ~11 GB (see [disk usage](#disk-usage-summary)) | none |
| SSH access to the server | — | split-host only |

**OS support today:**

| OS | Server | Client |
|---|---|---|
| Linux | yes — primary development/verification platform | yes |
| macOS | yes, in principle (no known blocker) — not yet live-verified | yes |
| Windows | yes, natively — needs a C/C++ compiler installed first (nothing unusual — the same class of prerequisite as `build-essential` on Linux), see [step 4](#4-install-system-packages). Not yet live-verified end-to-end; WSL2 remains a fallback if you'd rather not install a compiler on Windows directly | yes — this is the actively-used dev client platform |

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

**Windows** (server and client):

Download and install Python 3.13+ from [python.org](https://www.python.org/downloads/),
then:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## 4. Install system packages

Only install what your role/OS needs — server and client have almost no overlap.

### Server: PostgreSQL + pgvector

**Option A — native install (recommended)**: follow
[PostgreSQL's own download/install guide](https://www.postgresql.org/download/) for
your OS — it covers Windows, every major Linux distro, and macOS, and knows its own
installer landscape better than we could document here. Then install the `pgvector`
extension the same way: [pgvector's install instructions](https://github.com/pgvector/pgvector#installation)
cover Linux (`apt`/`yum` packages), macOS (`brew install pgvector`), and Windows
(build from source with the MSVC toolchain — the same "Desktop development with C++"
workload you'd install in [step 4](#4-install-system-packages) for `curated-tokenizers`
covers this too).

Once both are installed, create the role/database (the setup wizard verifies the
connection interactively in a later step — this just needs to exist):

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
<summary>Recommended: SSPI authentication (Windows, no password stored anywhere)</summary>

Peer auth itself doesn't exist on Windows — PostgreSQL's docs are explicit that it
needs `getpeereid()`/`SO_PEERCRED`, which Windows doesn't provide. But Windows has its
own OS-credential mechanism, **SSPI** (Windows' native single sign-on API, negotiating
Kerberos where available and falling back to NTLM otherwise), which PostgreSQL has
supported for exactly this purpose since early versions, including for a local,
non-domain-joined account on a standalone machine like a personal laptop — it isn't
Active-Directory-only. It works the same way peer auth does, just over a loopback TCP
connection instead of a Unix socket (Windows has no peer-credential-bearing socket to
use):

1. Add to `pg_ident.conf` (typically
   `C:\Program Files\PostgreSQL\<version>\data\pg_ident.conf`):
   ```
   # MAPNAME    SYSTEM-USERNAME    PG-USERNAME
   memai_map    <your-windows-username>    memai
   ```
   On a non-domain machine this is just your plain Windows username (no `\` or `@`
   prefix/suffix needed).
2. Add to `pg_hba.conf`, **above** any catch-all `host` line (first match wins):
   ```
   host   memai   memai   127.0.0.1/32   sspi map=memai_map
   host   memai   memai   ::1/128        sspi map=memai_map
   ```
3. Restart the PostgreSQL service (Services app, or an admin PowerShell:
   `Restart-Service postgresql-x64-<version>`).
4. Connection string: `postgresql://memai@localhost:5432/memai` — SSPI needs an
   explicit `host` (TCP), unlike peer's empty-host Unix socket.

The setup wizard offers this as the default option on Windows and will print these
exact instructions (with your actual Windows username filled in) if the mapping isn't
there yet.
</details>

<details>
<summary>Alternative: password authentication (remote Postgres, Docker, or if you'd rather not set up peer/SSPI auth)</summary>

```bash
sudo -u postgres psql -c "ALTER USER memai WITH PASSWORD 'changeme';"
```

Replace `changeme` with a password of your choice — the setup wizard prompts for
host/port/user/password and builds the connection string for you. This is the only
option for a Postgres instance on a different machine (peer/SSPI auth are inherently
local-only), and the Docker path below already sets a password this way.
</details>

**Option B — Docker (works the same on Linux/macOS/Windows, sidesteps installing
Postgres and pgvector separately)**: on Windows, install
[Docker Desktop](https://www.docker.com/products/docker-desktop/) first (it uses the
WSL2 backend automatically) — the `docker` commands below are then identical in
PowerShell.

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
`-v /opt/memai/pgdata:/var/lib/postgresql/data` to the `docker run` command. Skip the
role/database creation above — the image's env vars already did it — and use password
authentication (`memai`/`memai`) in the wizard.

### Server: Ollama

Follow [Ollama's own installer](https://ollama.com/download) for your OS (Windows,
macOS, or Linux) — same reasoning as Postgres above: it knows its own packaging better
than we could document here. On Linux this is the one-line shell installer:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama list    # should return an empty table on a fresh install
```

Ollama runs the offline memory pipeline regardless of which LLM powers live
conversation — see FR-707 in the wizard step below — so it's required even on a
GPU-less machine that will use a remote LLM for the live path.

### Server: espeak-ng and build tools (Linux/macOS)

```bash
sudo apt update
sudo apt install -y espeak-ng build-essential
```

`espeak-ng` is the phonemiser backend Kokoro TTS uses for non-English languages.
`build-essential` is needed because Kokoro's English G2P pipeline
(`kokoro`→`misaki[en]`→`spacy-curated-transformers`→`curated-tokenizers`) has no
prebuilt wheel and compiles a small Cython/C++ extension during `uv sync` — this is
normal and happens on every OS, not a sign anything is wrong.

On macOS: `brew install espeak-ng` (Xcode Command Line Tools, `xcode-select
--install`, provide the compiler).

### Server: C++ Build Tools (Windows)

Windows has no C/C++ compiler by default, which `curated-tokenizers` (see above) needs
to build during `uv sync`. Without it you'll see:

```
error: Microsoft Visual C++ 14.0 or greater is required. Get it with "Microsoft C++
Build Tools": https://visualstudio.microsoft.com/visual-cpp-build-tools/
```

Install it once, either via the linked installer (select the **"Desktop development
with C++"** workload) or from an admin PowerShell with
[winget](https://learn.microsoft.com/en-us/windows/package-manager/winget/):

```powershell
winget install --id Microsoft.VisualStudio.2022.BuildTools --override `
  "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

This is a one-time, ~4–7 GB install and requires an admin account — if you don't have
one (e.g. a locked-down corporate laptop), run the server inside WSL2 instead (see
[Windows without admin rights](#windows-without-admin-rights) below).

`espeak-ng` needs no separate install on Windows — the `espeakng-loader` Python package
(pulled in automatically) bundles the library as a wheel.

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

**Server** (any OS — on Windows, install [step 4](#4-install-system-packages)'s C++
Build Tools first):

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
.venv/bin/memai-setup       # .venv\Scripts\memai-setup on Windows
```

`memai-setup` is a separate package (its own venv), interactive, and safe to re-run — it
detects an existing install and pre-fills your previous choices rather than starting
over. It walks through, in order:

- **Topology** — single-host or split-host (matching what you picked in step 1).
- **Database connection** — verifies the Postgres role/database from step 4 (peer auth
  on Linux/macOS, SSPI on Windows, or host+password).
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
- **LLM provider** — local via Ollama (default) or a remote OpenAI-compatible HTTP
  endpoint (FR-707) for machines without a GPU capable of fast live inference — e.g. a
  laptop pointed at Claude, OpenAI, OpenRouter, or a self-hosted endpoint. This only
  affects the live conversational path; the offline memory pipeline (consolidation,
  memory briefs, persona-strategy helpers) always runs on the local Ollama model chosen
  above regardless. Needs a base URL and a model name; an API key is optional.
- **Languages** — pick every language you want supported now (main plus any secondary
  ones); which one is *primary* is chosen live during your first conversation.
- **STT model** — picks and pre-downloads a Whisper model size.
- **TTS voices** — downloads a voice per selected language.
- **Embedding model** — pre-downloads `multilingual-e5-large` (used for memory
  consolidation).
- **Config file** — writes `~/.config/memai/memai.toml` (`%LOCALAPPDATA%\memai\` on
  Windows), 0600 permissions where supported; shared by server and client on a
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
.\scripts\run-local.ps1         # native Windows server + client, single-host
```

**Split-host** — start each independently.

Server:

```bash
cd memai/server
.venv/bin/memai-server      # .venv\Scripts\memai-server.exe on Windows
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

## Windows without admin rights

Running `memai-server` natively on Windows needs one thing beyond what Linux/macOS
need: a C/C++ compiler ([step 4](#4-install-system-packages)), because Kokoro's
English G2P pipeline compiles a small Cython/C++ extension
(`curated-tokenizers`) during `uv sync`. Installing the compiler (Microsoft C++ Build
Tools) requires an admin account.

If you don't have one — e.g. a locked-down corporate laptop — run the server inside
**WSL2** (Ubuntu) instead, which ships a compiler already: this follows the documented
Linux steps above unmodified and gets you a genuine single-host setup on Windows
hardware, with the native Windows client talking to the WSL2 server over `localhost`
(modern WSL2 forwards `localhost` ports to Windows automatically) or over the same
SSH-tunnel mechanism as any split-host client if `localhost` forwarding isn't enabled on
your WSL2 version.

The native Windows client (this document's default assumption everywhere else) has no
such requirement — PortAudio ships prebuilt — and is already the actively-used
development platform for `client/`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `manifest unknown` on `docker pull` | Wrong image tag | Use `pgvector/pgvector:pg16` (no `-ubuntu` suffix) |
| `ollama: connection refused` | Ollama not running | `systemctl start ollama` (Linux), or start the Ollama app (macOS/Windows) |
| `CUDA out of memory` | VRAM exhausted | Reduce `stt.compute_type` to `int8` in `memai.toml`; or pick a smaller LLM that fits alongside Whisper+Kokoro |
| No response heard, but no error logged | LLM model too large for VRAM, split across CPU/GPU and evicted when idle — cold reload stalls the next turn | Check `ollama ps` shows `100% GPU`; switch to a smaller model (e.g. `aya-expanse`) |
| Assistant seems to ramble or speaks oddly before answering | Reasoning model (e.g. `qwen3`) emitting `<think>` blocks that get spoken aloud | Use a non-reasoning model; `think: false` does not suppress this on thinking-tuned models |
| Kokoro TTS silent / error | `espeak-ng` missing | Install it (see step 4) |
| `error: Microsoft Visual C++ 14.0 or greater is required` on `uv sync` (Windows) | No C/C++ compiler present | Install Microsoft C++ Build Tools (step 4), or use WSL2 for the server if you lack admin rights — see [Windows without admin rights](#windows-without-admin-rights) |
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
