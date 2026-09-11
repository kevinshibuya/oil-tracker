# pyright: reportUnknownMemberType=false
#
# pyarrow's stubs erase element types; see oiltracker.ais.readers.
"""What the manifest records for a day that works, repeats, fails, or was never published."""

import io
import zipfile
from datetime import date
from pathlib import Path

import httpx
import psycopg
import pyarrow.parquet as pq
import pytest
from psycopg.rows import TupleRow

from oiltracker.ais.backfill import (
    backfill_day,
    days_between,
    parquet_path,
    pending_days,
    read_manifest,
)
from oiltracker.ais.sources import SourceSpec
from oiltracker.ports.config import PortConfig, load_port

DAY = date(2024, 3, 5)

HEADER: str = (
    "MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,"
    "VesselType,Status,Length,Width,Draft,Cargo,TransceiverClass"
)
INSIDE = (
    "367000001,2024-03-05T00:00:00,27.8200,-97.2000,0.1,180.0,181.0,ALPHA,IMO9200001,WABC,"
    "80,5,250.0,44.0,15.5,80,A"
)
OUTSIDE = (
    "367000002,2024-03-05T00:05:00,25.0000,-90.0000,12.0,90.0,91.0,BRAVO,9200002,WDEF,"
    "80,0,180.0,32.0,9.0,80,A"
)


def columns(header: str) -> list[str]:
    return header.split(",")


PRIMARY = SourceSpec(
    url_pattern="https://primary.invalid/{Y}/AIS_{Y}_{M}_{D}.zip",
    format="csv_zip",
    header=columns(HEADER),
)
SECONDARY = SourceSpec(
    url_pattern="https://secondary.invalid/{Y}/AIS_{Y}_{M}_{D}.zip",
    format="csv_zip",
    header=columns(HEADER),
)


@pytest.fixture
def cfg() -> PortConfig:
    return load_port("corpus_christi")


def day_zip() -> bytes:
    payload = ("\n".join([HEADER, INSIDE, OUTSIDE]) + "\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AIS_2024_03_05.csv", payload)
    return buffer.getvalue()


def client_serving(payload: bytes) -> httpx.Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def client_status(code: int) -> httpx.Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, content=b"")

    return httpx.Client(transport=httpx.MockTransport(handler))


def client_only(host: str, payload: bytes) -> httpx.Client:
    """200 with the payload for one host, 404 for every other."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == host:
            return httpx.Response(200, content=payload)
        return httpx.Response(404, content=b"")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_good_day_writes_parquet_and_a_done_row(
    db: psycopg.Connection[TupleRow], cfg: PortConfig, tmp_path: Path
) -> None:
    with client_serving(day_zip()) as client:
        row = backfill_day(db, cfg, [PRIMARY], DAY, tmp_path, client)

    assert row.status == "done"
    assert row.rows_kept == 1
    assert row.bytes is not None and row.bytes > 0
    assert row.finished_at is not None
    assert row.source_url == "https://primary.invalid/2024/AIS_2024_03_05.zip"

    written = parquet_path(tmp_path, cfg.id, DAY)
    assert written.exists()
    assert pq.read_table(written).num_rows == 1
    assert not written.with_suffix(".parquet.tmp").exists()


def test_a_done_day_with_its_parquet_is_not_fetched_again(
    db: psycopg.Connection[TupleRow], cfg: PortConfig, tmp_path: Path
) -> None:
    with client_serving(day_zip()) as client:
        _ = backfill_day(db, cfg, [PRIMARY], DAY, tmp_path, client)

    assert pending_days(db, cfg, [DAY], tmp_path) == []


def test_a_done_day_whose_parquet_vanished_is_fetched_again(
    db: psycopg.Connection[TupleRow], cfg: PortConfig, tmp_path: Path
) -> None:
    with client_serving(day_zip()) as client:
        _ = backfill_day(db, cfg, [PRIMARY], DAY, tmp_path, client)
    parquet_path(tmp_path, cfg.id, DAY).unlink()

    assert pending_days(db, cfg, [DAY], tmp_path) == [DAY]


def test_a_reader_that_raises_leaves_failed_and_no_parquet(
    db: psycopg.Connection[TupleRow], cfg: PortConfig, tmp_path: Path
) -> None:
    with client_status(500) as client, pytest.raises(httpx.HTTPStatusError):
        _ = backfill_day(db, cfg, [PRIMARY], DAY, tmp_path, client)

    row = read_manifest(db, cfg.id, DAY)
    assert row.status == "failed"
    assert row.error is not None
    assert row.finished_at is not None
    assert not parquet_path(tmp_path, cfg.id, DAY).exists()
    assert pending_days(db, cfg, [DAY], tmp_path) == [DAY]


def test_a_day_no_source_has_is_missing_and_counts_as_complete(
    db: psycopg.Connection[TupleRow], cfg: PortConfig, tmp_path: Path
) -> None:
    with client_status(404) as client:
        row = backfill_day(db, cfg, [PRIMARY, SECONDARY], DAY, tmp_path, client)

    assert row.status == "missing"
    assert row.finished_at is not None
    assert not parquet_path(tmp_path, cfg.id, DAY).exists()
    assert pending_days(db, cfg, [DAY], tmp_path) == []


def test_a_404_on_the_primary_falls_through_to_the_next_source(
    db: psycopg.Connection[TupleRow], cfg: PortConfig, tmp_path: Path
) -> None:
    with client_only("secondary.invalid", day_zip()) as client:
        row = backfill_day(db, cfg, [PRIMARY, SECONDARY], DAY, tmp_path, client)

    assert row.status == "done"
    assert row.source_url == "https://secondary.invalid/2024/AIS_2024_03_05.zip"
    assert row.rows_kept == 1


def test_days_between_is_inclusive_at_both_ends() -> None:
    span = days_between(date(2024, 2, 27), date(2024, 3, 2))
    assert span == [
        date(2024, 2, 27),
        date(2024, 2, 28),
        date(2024, 2, 29),
        date(2024, 3, 1),
        date(2024, 3, 2),
    ]
    assert len(days_between(date(2023, 1, 1), date(2025, 9, 30))) == 1004
