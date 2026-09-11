"""`migrate` applies each file once, in name order."""

from pathlib import Path

import psycopg
from psycopg.rows import TupleRow

from oiltracker.db.migrate import migrate


def table_exists(conn: psycopg.Connection[TupleRow], name: str) -> bool:
    row = conn.execute("select to_regclass(%s)", (name,)).fetchone()
    assert row is not None
    return row[0] is not None


def test_applies_each_file_once_in_order(db: psycopg.Connection[TupleRow], tmp_path: Path) -> None:
    (tmp_path / "0002_b.sql").write_text("create temp table mig_b (id integer);")
    (tmp_path / "0001_a.sql").write_text("create temp table mig_a (id integer);")

    assert migrate(db, tmp_path) == ["0001_a.sql", "0002_b.sql"]
    assert migrate(db, tmp_path) == []

    assert table_exists(db, "mig_a")
    assert table_exists(db, "mig_b")
