# Infrastructure Dependencies

System-level dependencies that must be installed **outside** the Python interpreter and its
third-party libraries. Python packages and auto-downloaded model files are excluded — they
are managed by `uv` or downloaded on first run.

---

## Server

Runs on a GPU-equipped Linux machine (Ubuntu; NVIDIA CUDA required).

### System installations

| Dependency | Purpose | Install |
|---|---|---|
| **NVIDIA drivers** | GPU access for STT, TTS, embeddings | Pre-installed on GPU machines; minimum driver version for CUDA 12.x |
| **Ollama** | LLM daemon — serves llama3.3 over a local REST API; `ollama` Python lib is just a client | `curl -fsSL https://ollama.ai/install.sh \| sh` |
| **llama3.3 model** | Main LLM for conversation + consolidation | `ollama pull llama3.3` (~9 GB) |
| **PostgreSQL 15+** | Persistent storage for personas, memory, conversations | `apt install postgresql-15` |
| **pgvector extension** | Vector similarity search inside PostgreSQL | `apt install postgresql-15-pgvector` — then `CREATE EXTENSION vector` |
| **espeak-ng** | Phonemizer backend used by Kokoro for all non-English languages (FR, ES, IT, PT, JA, KO, ZH) | `apt install espeak-ng` |

### Python toolchain

| Dependency | Purpose |
|---|---|
| Python 3.13+ | Runtime |
| `uv` | Package manager and venv |

### First-run model downloads (automatic, via Python libs)

| Model | Library | Size |
|---|---|---|
| Whisper medium | `faster-whisper` | ~1.5 GB |
| Kokoro voice model | `kokoro` | ~0.4 GB |
| `intfloat/multilingual-e5-large` | `sentence-transformers` | ~2 GB |

> CUDA runtime is bundled inside the CTranslate2 / PyTorch wheels — no separate CUDA
> toolkit installation needed; only the GPU driver is required.

### Setup checklist

```
1. Install NVIDIA drivers (if not present)
2. Install Ollama → ollama pull llama3.3
3. Install PostgreSQL 15+ + pgvector
4. Install espeak-ng
5. Install Python 3.13+ + uv
6. cd server && uv sync
7. psql -d memai -f migrations/001_initial_schema.sql
8. Insert User record (primary language set during first voice session)
```

**Total disk (approx.):** ~15 GB (models) + ~2 GB (venv/libs)

---

## Client

Runs on the user's machine (Windows currently; Linux planned).

### System installations

| Dependency | Purpose | Install |
|---|---|---|
| **OpenSSH client** | SSH tunnel to server (`ssh -N -L ...` run as subprocess) | Built into Windows 10 21H1+ as an Optional Feature; verify it is enabled in *Settings → Apps → Optional features* |

> `sounddevice` bundles PortAudio on Windows — no separate audio library needed.  
> `webrtcvad-wheels` ships pre-built wheels — no C compiler needed.

### Python toolchain

| Dependency | Purpose |
|---|---|
| Python 3.13+ | Runtime |
| `uv` | Package manager and venv |

### Setup checklist

```
1. Verify OpenSSH client is enabled (Windows Optional Features)
2. Install Python 3.13+ + uv
3. cd client && uv sync
4. Create .env with SSH_USER_HOST=<user@server-address>
   (WS_PORT defaults to 8765)
```

No GPU, no database, no large model downloads.

---

## Connection

The client auto-establishes an SSH tunnel (`localhost:8765 → server:8765`) before
opening the WebSocket. SSH key-based authentication must be configured manually:
generate a key pair on the client machine and add the public key to
`~/.ssh/authorized_keys` on the server.
