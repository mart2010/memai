# Dev-only: wipe consolidated memory data (DB) and raw session logs (JSONL) for a
# fresh start during local testing. See reset_dev_state.py for exactly what this
# does and does not touch. Never wire this into the setup wizard or ship it.
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonBin = Join-Path $RepoRoot "server\.venv\Scripts\python.exe"

if (-not (Test-Path $PythonBin)) {
    Write-Error "$PythonBin not found — run 'cd server; uv sync' first"
    exit 1
}

& $PythonBin (Join-Path $RepoRoot "scripts\reset_dev_state.py") @args
exit $LASTEXITCODE
