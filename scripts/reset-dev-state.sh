#!/usr/bin/env bash
# Dev-only: wipe consolidated memory data (DB) and raw session logs (JSONL) for a
# fresh start during local testing. See reset_dev_state.py for exactly what this
# does and does not touch. Never wire this into the setup wizard or ship it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$REPO_ROOT/server/.venv/bin/python3"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "error: $PYTHON_BIN not found — run 'cd server && uv sync' first" >&2
    exit 1
fi

exec "$PYTHON_BIN" "$REPO_ROOT/scripts/reset_dev_state.py" "$@"
