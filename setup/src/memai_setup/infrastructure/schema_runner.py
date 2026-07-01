# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from pathlib import Path

import psycopg

# Cross-package file read (not a Python import) — `setup` doesn't bundle its
# own copy of the schema, to avoid drift from the authoritative one in
# `server`. Assumes the standard monorepo layout (setup/ and server/ as
# siblings), which is what memai-setup is designed to run inside anyway.
_DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "server" / "migrations" / "001_initial_schema.sql"


class PsycopgSchemaRunner:
    """Applies migrations/001_initial_schema.sql. The SQL itself is written to
    be idempotent (`CREATE TABLE/INDEX IF NOT EXISTS`, `ON CONFLICT DO
    NOTHING`), so a straightforward re-apply on every wizard run is safe — no
    migration framework needed, matching the project's explicit non-goal."""

    def __init__(self, schema_path: Path = _DEFAULT_SCHEMA_PATH) -> None:
        self._schema_path = schema_path

    def apply_schema(self, database_url: str) -> None:
        sql = self._schema_path.read_text(encoding="utf-8")
        with psycopg.connect(database_url) as conn, conn.cursor() as cur:
            cur.execute(sql)
