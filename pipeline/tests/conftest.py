"""Shared fixtures. Tests never skip: an unreachable database fails the run."""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import TupleRow

from oiltracker.cli import MIGRATIONS_DIR
from oiltracker.db.connect import connect
from oiltracker.db.migrate import migrate
from oiltracker.settings import Settings

UNREACHABLE = "database unreachable: run docker compose -f infra/docker-compose.yml up -d"


@pytest.fixture
def db() -> Iterator[psycopg.Connection[TupleRow]]:
    """A migrated connection inside a transaction that is rolled back afterwards."""
    try:
        conn = connect(Settings())
    except psycopg.OperationalError as exc:
        pytest.fail(f"{UNREACHABLE} ({exc})")
    try:
        migrate(conn, MIGRATIONS_DIR)
        conn.commit()
        yield conn
    finally:
        conn.rollback()
        conn.close()
