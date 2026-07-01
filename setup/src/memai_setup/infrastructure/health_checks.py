# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import socket
import urllib.error
import urllib.request

import psycopg

from ..services.ports import HealthCheckResult


class PostgresHealthCheck:
    name = "Postgres"

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def check(self) -> HealthCheckResult:
        try:
            with psycopg.connect(self._database_url, connect_timeout=5):
                pass
        except psycopg.OperationalError as exc:
            return HealthCheckResult(self.name, ok=False, message=str(exc).strip())
        return HealthCheckResult(self.name, ok=True, message="reachable")


class PgvectorExtensionHealthCheck:
    """Distinct from PostgresHealthCheck: a reachable Postgres does not imply
    pgvector is installed on that host (migrations/001_initial_schema.sql's
    `CREATE EXTENSION IF NOT EXISTS vector` will fail if the extension package
    itself isn't present, e.g. `postgresql-16-pgvector` was never installed).
    Catching this before SetupSchema saves a confusing failure later."""

    name = "pgvector extension"

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def check(self) -> HealthCheckResult:
        try:
            with psycopg.connect(self._database_url, connect_timeout=5) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                installed = cur.fetchone() is not None
        except psycopg.OperationalError as exc:
            return HealthCheckResult(self.name, ok=False, message=f"could not connect to check: {exc}".strip())
        if installed:
            return HealthCheckResult(self.name, ok=True, message="installed")
        return HealthCheckResult(
            self.name,
            ok=False,
            message="not installed — install the pgvector package for your Postgres version",
        )


class OllamaHealthCheck:
    name = "Ollama"

    def __init__(self, host: str = "http://localhost:11434") -> None:
        self._host = host

    def check(self) -> HealthCheckResult:
        try:
            with urllib.request.urlopen(f"{self._host}/api/tags", timeout=5) as response:
                status = response.status
        except (urllib.error.URLError, TimeoutError) as exc:
            return HealthCheckResult(self.name, ok=False, message=str(exc))
        ok = status == 200
        return HealthCheckResult(self.name, ok=ok, message="running" if ok else f"unexpected status {status}")


class ServerWebSocketHealthCheck:
    """TODO: the original design calls for launching `memai-server` as a
    subprocess and verifying the WebSocket handshake end-to-end (STT/TTS
    models actually load, not just "port is open"). That needs to resolve the
    server package's own venv/entry point from setup/ — deferred, needs the
    GPU server to verify meaningfully anyway. For now this only checks whether
    something is already listening on the configured port — enough to catch
    "forgot to start the server" but not "server crashed during model load"."""

    name = "Server WebSocket"

    def __init__(self, host: str = "localhost", port: int = 8765) -> None:
        self._host = host
        self._port = port

    def check(self) -> HealthCheckResult:
        try:
            with socket.create_connection((self._host, self._port), timeout=5):
                pass
        except OSError as exc:
            return HealthCheckResult(self.name, ok=False, message=str(exc))
        return HealthCheckResult(self.name, ok=True, message=f"listening on {self._host}:{self._port}")
