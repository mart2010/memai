# Installing Memai

Memai has two runtime components plus an interactive setup wizard:

| Component | Runs on | Role |
|---|---|---|
| **Server** (`server/`) | A machine with an NVIDIA GPU (Ubuntu 22.04+ recommended) | Speech-to-text, LLM, text-to-speech, long-term memory |
| **Client** (`client/`) | Your everyday machine (Windows, macOS, Linux) | Microphone capture, audio playback |
| **Setup wizard** (`setup/`) | The server machine | One-time interactive install: picks models against your GPU, downloads everything, writes config, applies the DB schema |

## Choose your topology

**Split-host (typical)** — the server lives on a GPU workstation, the client on your
laptop. The client automatically opens an SSH tunnel to the server; no ports are exposed
beyond SSH.

**Single-host** — client and server on the same GPU machine. Both read the same
`memai.toml`, and `./scripts/run-local.sh` starts the server and launches the client in
one terminal. No SSH involved.

## At a glance

| Requirement | Server | Client |
|---|---|---|
| Python 3.13+ and [uv](https://docs.astral.sh/uv/) | yes | yes |
| NVIDIA GPU (≥ 8 GB VRAM, driver 525+) | yes | no |
| PostgreSQL 16 + pgvector | yes | no |
| Ollama | yes | no |
| Disk for models | ~20 GB | none |
| SSH access to the server | — | split-host only |

No CUDA toolkit install is needed (the Python wheels bundle the runtime), and after the
initial model downloads the whole system runs without internet access.

## Step-by-step guides

1. **[Server installation](INSTALL_SERVER.md)** — NVIDIA driver check, system packages
   (PostgreSQL + pgvector, Ollama, espeak-ng), `uv sync`, then the `memai-setup` wizard,
   which handles model selection/downloads, config, schema, and health checks.
2. **[Client installation](INSTALL_CLIENT.md)** — install the client, set up SSH key
   authentication to the server, and create the client's `memai.toml` (split-host), or
   skip the SSH part entirely (single-host).

First launch is self-guiding: the server walks you through language selection, and from
then on everything is configured by voice.
