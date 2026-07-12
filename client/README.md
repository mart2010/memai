# memai-client

The Memai client — the half of [Memai](../README.md) that runs on your everyday machine
(Windows, macOS, or Linux). It captures microphone audio, detects when you're speaking,
streams it to the server, and plays back the synthesised reply. It is deliberately thin
and stateless: no GPU, no database, no model downloads.

In a split-host deployment it automatically opens an SSH tunnel to the server before
connecting — nothing but SSH is ever exposed on the network.

## Install

Follow the [client installation guide](../docs/INSTALL_CLIENT.md). In short:

```bash
uv tool install "git+<repo-url>#subdirectory=client"
# create memai.toml in your platform config dir (see config/memai.example.toml)
memai-client
```

For development, from this directory:

```bash
uv sync
uv run memai-client
```

## Configuration

One file: `memai.toml` in your platform config directory
(`~/.config/memai/` on Linux/macOS, `%LOCALAPPDATA%\memai\` on Windows) — see
[config/memai.example.toml](config/memai.example.toml). `ssh_host` set means
split-host (tunnel to that machine); `ssh_host` omitted means the server is local.

See the [root README](../README.md) for what Memai is, and
[docs/INSTALLATION.md](../docs/INSTALLATION.md) for the full picture.
