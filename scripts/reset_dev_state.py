#!/usr/bin/env python3
"""Dev-only reset: wipe consolidated memory data and raw session logs so the next
session starts from nothing.

NOT part of the product — never wire this into the setup wizard or ship it. It
deliberately violates INV-5 ("session logs kept forever") as an explicit, opt-in
dev convenience for repeated local testing, not a default or automatic behaviour.

Truncates conversations/turns/episodes/concepts/procedures/memory_brief and deletes
every *.jsonl directly under the configured log_dir. Leaves users, personas, and
bundle_installs alone — persona/user config and install provenance survive a reset;
only accumulated memory and raw logs are cleared.

Run via the wrapper for your platform (resolves the right venv python), not directly:
    scripts/reset-dev-state.sh [--yes] [--dry-run]
    scripts/reset-dev-state.ps1 [--yes] [--dry-run]
"""
import argparse
import sys

import psycopg

from memai_server.infrastructure.config import CONFIG_PATH, load_config

_MEMORY_TABLES = ("conversations", "turns", "episodes", "concepts", "procedures", "memory_brief")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    args = parser.parse_args()

    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    log_files = sorted(config.log_dir.glob("*.jsonl")) if config.log_dir.exists() else []

    with psycopg.connect(config.database_url) as conn, conn.cursor() as cur:
        counts = {}
        for table in _MEMORY_TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 — table names are the fixed literal above, not input
            counts[table] = cur.fetchone()[0]

        print(f"Config:  {CONFIG_PATH}")
        print(f"Log dir: {config.log_dir}")
        print(f"  {len(log_files)} session log file(s) to delete")
        print("DB memory tables to truncate (users/personas/bundle_installs untouched):")
        for table, count in counts.items():
            print(f"  {table}: {count} row(s)")

        if not any(counts.values()) and not log_files:
            print("\nAlready clean — nothing to do.")
            return 0

        if args.dry_run:
            print("\n--dry-run: nothing changed.")
            return 0

        if not args.yes:
            try:
                reply = input("\nProceed? Type 'yes' to confirm: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                reply = ""
            if reply != "yes":
                print("\nAborted.")
                return 1

        for f in log_files:
            f.unlink()
        cur.execute(f"TRUNCATE TABLE {', '.join(_MEMORY_TABLES)} RESTART IDENTITY CASCADE")

    print(f"\nDone: deleted {len(log_files)} log file(s), truncated {len(_MEMORY_TABLES)} table(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
