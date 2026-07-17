# Convenience launcher for single-host setups (server and client on the same
# machine). Starts the server as a background job, waits for its WebSocket port
# to come up, then runs the client in the foreground; the server is stopped
# when the client exits. Split-host deployments should keep starting each
# package independently — see docs/INSTALLATION.md.
#
# Requires `uv sync` to have succeeded in server/, which on Windows needs a
# C/C++ compiler installed first — see docs/INSTALLATION.md's "Server: C++
# Build Tools (Windows)" section.
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$WsPort = if ($env:MEMAI_WS_PORT) { $env:MEMAI_WS_PORT } else { 8765 }
$ReadyTimeout = if ($env:MEMAI_READY_TIMEOUT) { [int]$env:MEMAI_READY_TIMEOUT } else { 300 }  # seconds; first run downloads ~4GB of models

$ServerBin = Join-Path $RepoRoot "server\.venv\Scripts\memai-server.exe"
$ClientBin = Join-Path $RepoRoot "client\.venv\Scripts\memai-client.exe"

foreach ($entry in @(@{Bin = $ServerBin; Pkg = "server" }, @{Bin = $ClientBin; Pkg = "client" })) {
    if (-not (Test-Path $entry.Bin)) {
        Write-Error "$($entry.Bin) not found — run 'cd $($entry.Pkg); uv sync' first"
        exit 1
    }
}

$serverJob = Start-Job -ScriptBlock { param($bin) & $bin } -ArgumentList $ServerBin

try {
    Write-Host "Waiting for server on :$WsPort (up to ${ReadyTimeout}s)..."
    $ready = $false
    for ($i = 0; $i -lt $ReadyTimeout; $i++) {
        if ($serverJob.State -ne "Running") {
            Write-Error "server exited before becoming ready"
            Receive-Job $serverJob
            exit 1
        }
        $test = Test-NetConnection -ComputerName 127.0.0.1 -Port $WsPort -WarningAction SilentlyContinue -InformationLevel Quiet
        if ($test) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }

    if (-not $ready) {
        Write-Error "server did not become ready within ${ReadyTimeout}s"
        exit 1
    }

    Write-Host "Server ready, starting client..."
    & $ClientBin
}
finally {
    Stop-Job $serverJob -ErrorAction SilentlyContinue | Out-Null
    Remove-Job $serverJob -Force -ErrorAction SilentlyContinue | Out-Null
}
