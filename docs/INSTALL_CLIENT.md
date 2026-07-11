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

No need to clone the full repository. Install directly from the Git repo as an isolated
tool:

```bash
uv tool install "git+<repo-url>#subdirectory=client"
```

This puts the `memai-client` command on your PATH in its own managed environment —
no manual venv, no working directory to keep track of.

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

## 5. Configure the client

Create `~/.config/memai/memai.toml` on Linux/macOS (`%LOCALAPPDATA%\memai\memai.toml`
on Windows) — same layout as `client/config/memai.example.toml` in the repo:

```toml
[server]
ws_port = 8765
ssh_host = "<user>@<server-address>"
```

`ssh_host` is what tells the client to tunnel — required for split-host (this guide).
`ws_port` only needs setting if the server uses a non-default port. This is the same
file the server reads its own settings from, so a single-host install (where
`memai-setup` writes it for you — see docs/INSTALL_SERVER.md) shares one file with
`ssh_host` simply omitted.

---

## 6. Start the client

```bash
memai-client
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
| `FileNotFoundError: Client config not found` | `memai.toml` missing | Create it as shown in step 5 |
| Client connects to `localhost` instead of tunneling | `ssh_host` missing/blank in `memai.toml` | Set `ssh_host` in `memai.toml` (step 5) |
| `Connection refused` on WebSocket | Server not running or tunnel failed | Start the server; check SSH key auth |
| SSH prompts for password | No key auth | Run `ssh-copy-id` (step 4) |
| No audio input detected | Wrong microphone or permissions | Check OS audio settings; grant mic permission |
| `OSError: PortAudio not found` | Missing PortAudio | Install via `brew install portaudio` or `apt install libportaudio2` |
