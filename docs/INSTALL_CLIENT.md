# Client Installation Guide

The Memai client runs on the user's machine and handles microphone capture and audio
playback. It connects to the server over an SSH tunnel.

Supported platforms: **Windows**, **macOS**, **Linux**.

---

## Requirements

| Component | Requirement |
|---|---|
| OS | Windows 10+, macOS 12+, or any modern Linux distro |
| Python | 3.13+ |
| Microphone | Any microphone recognised by the OS |
| Network | SSH access to the server machine (port 22) |

---

## 1. Install system dependencies

### Windows

No extra system packages needed — all Python wheels bundle their native dependencies.

OpenSSH is included in Windows 10+ and available in PowerShell/CMD. Verify:

```powershell
ssh -V
```

### macOS

Install PortAudio (required by `sounddevice`):

```bash
brew install portaudio
```

SSH is included with macOS — no extra install needed.

### Linux

Install PortAudio:

```bash
sudo apt install -y libportaudio2
```

SSH client is standard on all Linux distros. Verify:

```bash
ssh -V
```

---

## 2. Install Python 3.13+ and uv

### Windows

Download and install Python 3.13+ from [python.org](https://www.python.org/downloads/).

Install uv:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### macOS / Linux

```bash
curl -Lsf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env   # or restart your shell
```

---

## 3. Install the client package

No need to clone the full repository. Create a working directory, then install directly
from the Git repo:

```bash
mkdir memai-client && cd memai-client
uv venv
uv pip install "git+<repo-url>#subdirectory=client"
```

This installs the `memai-client` command into `.venv/` with all its dependencies.
The working directory is also where your `.env` file will live (step 5).

---

## 4. Set up SSH key authentication

The client opens an SSH tunnel to the server automatically on startup. Password-based
SSH will prompt on every connect — key-based auth is strongly recommended.

**Generate a key (skip if you already have one):**

```bash
ssh-keygen -t ed25519 -C "memai-client"
```

**Copy the public key to the server:**

```bash
ssh-copy-id <user>@<server-address>
```

On Windows, if `ssh-copy-id` is not available:

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh <user>@<server-address> "cat >> ~/.ssh/authorized_keys"
```

**Verify the connection works without a password:**

```bash
ssh <user>@<server-address> echo ok
```

---

## 5. Configure environment variables

Create a `.env` file in `memai/client/`:

```dotenv
# Required — SSH target for the tunnel (user@hostname or user@ip)
SSH_USER_HOST=<user>@<server-address>

# Optional — must match WS_PORT on the server (default: 8765)
# WS_PORT=8765
```

---

## 6. Start the client

### Windows

```powershell
cd memai-client
.venv\Scripts\memai-client
```

### macOS / Linux

```bash
cd memai-client
.venv/bin/memai-client
```

Expected output:

```
Starting SSH tunnel...
Connected
```

The server must be running before the client starts. On first connect, if no primary
language is configured on the server, a language selection prompt appears in the terminal.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `SSH_USER_HOST` not set | Missing `.env` | Create `.env` with `SSH_USER_HOST=...` |
| `Connection refused` on WebSocket | Server not running or tunnel failed | Start the server; check SSH key auth |
| SSH prompts for password | No key auth | Run `ssh-copy-id` (step 4) |
| No audio input detected | Wrong microphone or permissions | Check OS audio settings; grant mic permission |
| `OSError: PortAudio not found` | Missing PortAudio | Install via `brew install portaudio` or `apt install libportaudio2` |
