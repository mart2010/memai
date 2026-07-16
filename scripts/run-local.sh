#!/usr/bin/env bash
# Convenience launcher for single-host setups (server and client on the same
# machine). Starts the server in the background, waits for its WebSocket port
# to come up, then runs the client in the foreground; the server is killed
# when the client exits. Split-host deployments should keep starting each
# package independently — see docs/INSTALLATION.md. Windows equivalent:
# run-local.ps1.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WS_PORT="${MEMAI_WS_PORT:-8765}"
READY_TIMEOUT="${MEMAI_READY_TIMEOUT:-300}"  # seconds; first run downloads ~4GB of models

SERVER_BIN="$REPO_ROOT/server/.venv/bin/memai-server"
CLIENT_BIN="$REPO_ROOT/client/.venv/bin/memai-client"

for entry in "$SERVER_BIN:server" "$CLIENT_BIN:client"; do
    bin="${entry%%:*}"
    pkg="${entry##*:}"
    if [[ ! -x "$bin" ]]; then
        echo "error: $bin not found — run 'cd $pkg && uv sync' first" >&2
        exit 1
    fi
done

"$SERVER_BIN" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM

echo "Waiting for server on :$WS_PORT (up to ${READY_TIMEOUT}s)..."
ready=0
for ((i = 0; i < READY_TIMEOUT; i++)); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "error: server exited before becoming ready" >&2
        exit 1
    fi
    if (exec 3<>"/dev/tcp/127.0.0.1/$WS_PORT") 2>/dev/null; then
        exec 3<&- 3>&-
        ready=1
        break
    fi
    sleep 1
done

if [[ "$ready" -ne 1 ]]; then
    echo "error: server did not become ready within ${READY_TIMEOUT}s" >&2
    exit 1
fi

echo "Server ready, starting client..."
"$CLIENT_BIN"
