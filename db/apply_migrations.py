"""Forward-only numbered migration applier for djinni_market.

Pattern copied (not imported) from finance-portal's `_apply_nocobase_bi_migration.py`:
numbered files in db/migrations/, tracked in meta.schema_migrations, applied once each,
in order, newest-first-missing only (never re-applies, never rolls back).

Connect as the postgres superuser (or any role granted membership in djinni_owner) with
dbname=djinni_market; this script issues `SET ROLE djinni_owner` so every created object
is owned by djinni_owner. DSN comes from the DJINNI_PG_DSN env var — never printed, never
passed on argv.

    python db/apply_migrations.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def applied_versions(cur) -> set[str]:
    cur.execute(
        "SELECT to_regclass('meta.schema_migrations') IS NOT NULL"
    )
    if not cur.fetchone()[0]:
        return set()
    cur.execute("SELECT version FROM meta.schema_migrations")
    return {row[0] for row in cur.fetchall()}


def apply_all(dsn: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every not-yet-applied migration file in order. Returns the list applied."""
    files = sorted(migrations_dir.glob("*.sql"))
    applied = []
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SET ROLE djinni_owner")
            done = applied_versions(cur)
        for f in files:
            if f.name in done:
                continue
            sql = f.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute("SET ROLE djinni_owner")
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO meta.schema_migrations (version) VALUES (%s)", (f.name,)
                )
            conn.commit()
            applied.append(f.name)
    return applied


def main(argv=None) -> int:
    dsn = os.environ.get("DJINNI_PG_DSN")
    if not dsn:
        print("DJINNI_PG_DSN is not set", file=sys.stderr)
        return 2
    applied = apply_all(dsn)
    if applied:
        print(f"applied: {', '.join(applied)}")
    else:
        print("nothing to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
