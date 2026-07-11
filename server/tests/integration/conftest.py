# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Fixtures for real-infrastructure integration tests (Postgres, STT, TTS, embedding).

Each fixture skips its dependent tests gracefully when the required service or model
isn't reachable, so `pytest` stays runnable on machines without a GPU/DB (e.g. the dev
laptop) — only the GPU workstation is expected to run these for real.
"""
import os
from pathlib import Path

import psycopg
import pytest

from memai_server.infrastructure.postgres import connect

_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
_TEST_DB_URL = os.environ.get(
    "MEMAI_TEST_DATABASE_URL", "postgresql://memai:memai@localhost:5432/memai_test"
)

# Dedicated database, never the dev `memai` database — integration tests truncate all
# tables between runs, which would destroy real conversation history if pointed there.
_TABLES_TO_TRUNCATE = (
    "turns", "conversations", "episodes", "concepts", "procedures", "memory_brief",
    "personas", "users", "bundle_installs",
)


def _maintenance_url(dsn: str) -> str:
    base, _, _dbname = dsn.rpartition("/")
    return f"{base}/postgres"


def _ensure_test_database_exists(dsn: str) -> None:
    dbname = dsn.rpartition("/")[2]
    with psycopg.connect(_maintenance_url(dsn), autocommit=True, connect_timeout=5) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{dbname}"')


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """Real Postgres test database with the current schema applied once per test
    session (drop/recreate `public`, matching the project's documented "no migration
    framework, just re-apply" approach — see PLAN.md Phase 8)."""
    try:
        _ensure_test_database_exists(_TEST_DB_URL)
        # Plain connection, not `connect()` — that registers the pgvector type
        # immediately, which fails until the migration below creates the extension.
        conn = psycopg.connect(_TEST_DB_URL, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not reachable at {_TEST_DB_URL}: {e}")

    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute(_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.close()
    return _TEST_DB_URL


@pytest.fixture
def pg_conn(pg_dsn: str) -> psycopg.Connection:
    """Fresh connection per test; all app tables truncated first for isolation
    (including the GA seed row — tests that need a persona create their own)."""
    conn = connect(pg_dsn)
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(_TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
    yield conn
    conn.close()
