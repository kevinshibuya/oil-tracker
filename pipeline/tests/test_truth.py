"""Parsing the PortWatch feature service, and writing it idempotently."""

import json
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import httpx
import psycopg
from psycopg.rows import TupleRow

from oiltracker.truth.portwatch import (
    PortwatchRow,
    confirm_portid,
    fetch_portwatch,
    upsert_portwatch,
)

PORTID = "port264"
# The real series lives under port264 and is committed; the write test uses its own.
TEST_PORTID = "porttest"

# Epoch milliseconds at midnight UTC, and tonnages as the floats the service sends.
PAGE_ONE = {
    "features": [
        {
            "attributes": {
                "date": 1672531200000,  # 2023-01-01
                "portid": PORTID,
                "portcalls": 12.0,
                "portcalls_tanker": 7.0,
                "export_tanker": 412345.67,
                "import_tanker": 1234.4,
            }
        },
        {
            "attributes": {
                "date": 1672617600000,  # 2023-01-02
                "portid": PORTID,
                "portcalls": 9.0,
                "portcalls_tanker": 5.0,
                "export_tanker": 300000.5,
                "import_tanker": None,
            }
        },
    ],
    "exceededTransferLimit": True,
}

PAGE_TWO = {
    "features": [
        {
            "attributes": {
                "date": 1672704000000,  # 2023-01-03
                "portid": PORTID,
                "portcalls": 11.0,
                "portcalls_tanker": 6.0,
                "export_tanker": 250000.49,
                "import_tanker": 900.0,
            }
        }
    ],
    "exceededTransferLimit": False,
}

# What the live service sends today: the date field is esriFieldTypeDateOnly.
ISO_PAGE = {
    "features": [
        {
            "attributes": {
                "date": "2023-01-02",
                "portid": PORTID,
                "portcalls": 5,
                "portcalls_tanker": 5,
                "export_tanker": 155049,
                "import_tanker": 117571,
            }
        }
    ],
    "exceededTransferLimit": False,
}

NAME_PAGE = {
    "features": [{"attributes": {"portid": PORTID, "portname": "Corpus Christi"}}],
    "exceededTransferLimit": False,
}


def paging_client(pages: Sequence[Mapping[str, Any]]) -> httpx.Client:
    """Answers each successive request with the next page."""
    served = iter(pages)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(next(served)).encode())

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_fetcher_follows_paging_and_converts_dates_and_tonnes() -> None:
    with paging_client([PAGE_ONE, PAGE_TWO]) as client:
        rows = fetch_portwatch(client, PORTID, date(2023, 1, 1))

    assert [row.date for row in rows] == [date(2023, 1, 1), date(2023, 1, 2), date(2023, 1, 3)]
    assert [row.portcalls_tanker for row in rows] == [7, 5, 6]
    # Floats in tonnes, stored rounded to the nearest integer.
    assert [row.export_tanker_t for row in rows] == [412346, 300000, 250000]
    assert [row.import_tanker_t for row in rows] == [1234, None, 900]
    assert {row.portid for row in rows} == {PORTID}


def test_a_single_page_stops_without_a_second_request() -> None:
    with paging_client([PAGE_TWO]) as client:
        rows = fetch_portwatch(client, PORTID, date(2023, 1, 1))
    assert len(rows) == 1


def test_an_iso_date_string_is_read_as_well_as_epoch_millis() -> None:
    with paging_client([ISO_PAGE]) as client:
        rows = fetch_portwatch(client, PORTID, date(2023, 1, 1))

    assert [row.date for row in rows] == [date(2023, 1, 2)]
    assert rows[0].export_tanker_t == 155049


def test_confirm_portid_returns_the_matching_pairs() -> None:
    with paging_client([NAME_PAGE]) as client:
        assert confirm_portid(client, "Corpus") == [(PORTID, "Corpus Christi")]


def test_the_upsert_is_idempotent(db: psycopg.Connection[TupleRow]) -> None:
    rows = [
        PortwatchRow(date(2023, 1, 1), TEST_PORTID, 12, 7, 412346, 1234),
        PortwatchRow(date(2023, 1, 2), TEST_PORTID, 9, 5, 300000, None),
    ]

    assert upsert_portwatch(db, rows) == 2
    assert _count(db) == 2

    assert upsert_portwatch(db, rows) == 2
    assert _count(db) == 2

    changed = [PortwatchRow(date(2023, 1, 1), TEST_PORTID, 13, 8, 999999, 0)]
    _ = upsert_portwatch(db, changed)
    assert _count(db) == 2

    row = db.execute(
        "select portcalls, export_tanker_t from portwatch_daily where date = %s and portid = %s",
        (date(2023, 1, 1), TEST_PORTID),
    ).fetchone()
    assert row is not None
    assert row == (13, 999999)


def _count(conn: psycopg.Connection[TupleRow]) -> int:
    row = conn.execute(
        "select count(*) from portwatch_daily where portid = %s", (TEST_PORTID,)
    ).fetchone()
    assert row is not None
    return int(row[0])
