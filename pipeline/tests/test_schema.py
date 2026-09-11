"""The core schema: the eight tables, and the monthly partitioning of positions."""

from typing import Final, LiteralString

import psycopg
import pytest
from psycopg.rows import TupleRow

CORE_TABLES = [
    "ingest_manifest",
    "vessels",
    "vessel_statics_daily",
    "positions",
    "terminals",
    "berth_visits",
    "export_events",
    "portwatch_daily",
]

# 2023-01 through 2025-09 inclusive.
EXPECTED_PARTITIONS: Final = 33

INSERT_POSITION: Final = """
insert into positions (port, mmsi, ts, geom, sog)
values ('corpus_christi', 367000001, %s, st_setsrid(st_makepoint(-97.2, 27.8), 4326), 0.1)
"""


def scalar(
    conn: psycopg.Connection[TupleRow], sql: LiteralString, params: tuple[object, ...] = ()
) -> object:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


@pytest.mark.parametrize("table", CORE_TABLES)
def test_core_table_exists(db: psycopg.Connection[TupleRow], table: str) -> None:
    assert scalar(db, "select to_regclass(%s)", (table,)) is not None


def test_positions_is_partitioned_by_month(db: psycopg.Connection[TupleRow]) -> None:
    count = scalar(
        db,
        "select count(*) from pg_inherits where inhparent = 'positions'::regclass",
    )
    assert count == EXPECTED_PARTITIONS


def test_last_instant_of_the_span_routes_to_september_2025(
    db: psycopg.Connection[TupleRow],
) -> None:
    _ = db.execute(INSERT_POSITION, ("2025-09-30 23:59:00+00",))
    where = scalar(
        db,
        "select tableoid::regclass::text from positions where mmsi = 367000001",
    )
    assert where == "positions_2025_09"


def test_instant_past_the_span_has_no_partition(db: psycopg.Connection[TupleRow]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        _ = db.execute(INSERT_POSITION, ("2025-10-01 00:00:00+00",))
