"""Ordered plain-SQL migrations, applied once each."""

import re
from pathlib import Path
from typing import LiteralString, cast

import psycopg
from psycopg.rows import TupleRow

MIGRATION_NAME = re.compile(r"^\d{4}_.+\.sql$")

LEDGER = """
create table if not exists schema_migrations (
    name text primary key,
    applied_at timestamptz not null default now())
"""


def migrate(conn: psycopg.Connection[TupleRow], migrations_dir: Path) -> list[str]:
    """Apply every `NNNN_*.sql` in `migrations_dir` not yet recorded, in name order.

    Returns the names applied by this call, empty when the store is current.

    The caller owns the transaction. Nothing here commits, so a test can migrate
    a throwaway directory and roll the ledger back with the rest of its work.
    """
    _ = conn.execute(LEDGER)
    done: set[str] = {row[0] for row in conn.execute("select name from schema_migrations")}

    applied: list[str] = []
    candidates = (p for p in migrations_dir.iterdir() if MIGRATION_NAME.match(p.name))
    for path in sorted(candidates, key=lambda p: p.name):
        if path.name in done:
            continue
        # Migrations are repository content, never user input, so the cast to
        # LiteralString is sound. With no parameters psycopg uses the simple
        # protocol, so one file may hold several statements.
        _ = conn.execute(cast(LiteralString, path.read_text()))
        _ = conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
        applied.append(path.name)
    return applied
