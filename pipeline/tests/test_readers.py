# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false
#
# As in oiltracker.ais.readers: pyarrow's stubs erase element types, and this
# test builds Arrow tables by hand.
"""Reading a day out of each archive format, and nulling the not-reported codes."""

import io
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pyarrow as pa
import pytest
import zstandard

from oiltracker.ais.readers import StreamStats, iter_chunks, normalise
from oiltracker.ais.schema import CANONICAL_COLUMNS, canonical_for
from oiltracker.ais.sources import SourceSpec
from oiltracker.ports.config import BBox

HEADER_ZIP: str = (
    "MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,"
    "VesselType,Status,Length,Width,Draft,Cargo,TransceiverClass"
)
HEADER_ZST: str = (
    "mmsi,base_date_time,longitude,latitude,sog,cog,heading,vessel_name,imo,call_sign,"
    "vessel_type,status,length,width,draft,cargo,transceiver"
)

# One row inside the Corpus Christi box, one far away in the Gulf.
ROWS_ZIP = [
    "367000001,2024-03-05T00:00:00,27.8200,-97.2000,0.1,180.0,181.0,ALPHA,IMO9200001,WABC,"
    "80,5,250.0,44.0,15.5,80,A",
    "367000002,2024-03-05T00:05:00,25.0000,-90.0000,12.0,90.0,91.0,BRAVO,9200002,WDEF,"
    "80,0,180.0,32.0,9.0,80,A",
]
ROWS_ZST = [
    "367000001,2024-03-05T00:00:00,-97.2000,27.8200,0.1,180.0,181.0,ALPHA,IMO9200001,WABC,"
    "80,5,250.0,44.0,15.5,80,A",
    "367000002,2024-03-05T00:05:00,-90.0000,25.0000,12.0,90.0,91.0,BRAVO,9200002,WDEF,"
    "80,0,180.0,32.0,9.0,80,A",
]

BBOX = BBox(lat_min=27.55, lat_max=28.05, lon_min=-97.60, lon_max=-96.60)


def csv_bytes(header: str, rows: list[str]) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


def zipped(payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AIS_2024_03_05.csv", payload)
    return buffer.getvalue()


def serving(payload: bytes) -> httpx.Client:
    """A client that answers any GET with these bytes."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def read_all(spec: SourceSpec, payload: bytes, stats: StreamStats | None = None) -> pa.Table:
    with serving(payload) as client:
        blocks = list(iter_chunks("https://example.invalid/day", spec, client, BBOX, 2024, stats))
    return pa.concat_tables(blocks)


def test_the_zip_reader_keeps_only_the_bounding_box() -> None:
    spec = SourceSpec(
        url_pattern="https://example.invalid/{Y}/AIS_{Y}_{M}_{D}.zip",
        format="csv_zip",
        header=HEADER_ZIP.split(","),
    )
    table = read_all(spec, zipped(csv_bytes(HEADER_ZIP, ROWS_ZIP)))

    assert table.column_names == CANONICAL_COLUMNS
    assert table.num_rows == 1
    assert table.column("mmsi").to_pylist() == [367000001]
    assert table.column("ts").to_pylist() == [datetime(2024, 3, 5, tzinfo=UTC)]
    assert table.column("imo").to_pylist() == [9200001]
    assert table.column("draught_m").to_pylist() == [pytest.approx(15.5)]


def test_the_zst_reader_handles_the_snake_case_header_and_lon_first_order() -> None:
    spec = SourceSpec(
        url_pattern="https://example.invalid/csv{Y}/ais-{Y}-{M}-{D}.csv.zst",
        format="csv_zst",
        header=HEADER_ZST.split(","),
    )
    payload = zstandard.ZstdCompressor().compress(csv_bytes(HEADER_ZST, ROWS_ZST))
    stats = StreamStats()
    table = read_all(spec, payload, stats)

    assert table.num_rows == 1
    assert table.column("mmsi").to_pylist() == [367000001]
    # Longitude comes first in this header; mapping is by name, so it still lands right.
    assert table.column("lat").to_pylist() == [pytest.approx(27.82)]
    assert table.column("lon").to_pylist() == [pytest.approx(-97.20)]
    assert stats.bytes_read == len(payload)


def test_a_parquet_spec_is_refused_because_the_probe_found_none() -> None:
    spec = SourceSpec(
        url_pattern="https://example.invalid/{Y}.parquet", format="parquet", header=[]
    )
    with pytest.raises(NotImplementedError), serving(b"") as client:
        _: Iterator[pa.Table] = iter_chunks("https://example.invalid/x", spec, client, BBOX, 2024)
        next(_)


SENTINEL_ROW = (
    "367000003,2024-03-05T01:00:00,27.8200,-97.2000,102.3,360.0,511.0,CHARLIE,NOTANIMO,WGHI,"
    "80,0,0.0,0.0,0.0,80,A"
)


@pytest.mark.parametrize(
    "column",
    ["sog", "cog", "heading", "length_m", "width_m", "draught_m", "imo"],
)
def test_each_not_reported_code_becomes_null(column: str) -> None:
    mapping = canonical_for(HEADER_ZIP.split(","), 2024)
    spec = SourceSpec(
        url_pattern="https://example.invalid/{Y}/AIS_{Y}_{M}_{D}.zip",
        format="csv_zip",
        header=HEADER_ZIP.split(","),
    )
    table = read_all(spec, zipped(csv_bytes(HEADER_ZIP, [SENTINEL_ROW])))

    assert set(mapping.values()) >= {column} or column == "imo"
    assert table.num_rows == 1
    assert table.column(column).to_pylist() == [None]


def test_a_plain_numeric_imo_survives() -> None:
    spec = SourceSpec(
        url_pattern="https://example.invalid/{Y}/AIS_{Y}_{M}_{D}.zip",
        format="csv_zip",
        header=HEADER_ZIP.split(","),
    )
    row = ROWS_ZIP[0].replace("IMO9200001", "9200001")
    table = read_all(spec, zipped(csv_bytes(HEADER_ZIP, [row])))

    assert table.column("imo").to_pylist() == [9200001]


def test_a_header_without_coordinates_is_a_hard_error_naming_the_year() -> None:
    with pytest.raises(ValueError, match="2025"):
        _ = canonical_for(["MMSI", "BaseDateTime", "SOG"], 2025)


def test_normalise_fills_columns_the_archive_does_not_carry() -> None:
    header = ["MMSI", "BaseDateTime", "LAT", "LON"]
    mapping = canonical_for(header, 2024)
    block = pa.table(
        {
            "MMSI": pa.array([367000001], pa.int32()),
            "BaseDateTime": pa.array([datetime(2024, 3, 5)], pa.timestamp("us")),
            "LAT": pa.array([27.82], pa.float64()),
            "LON": pa.array([-97.20], pa.float64()),
        }
    )
    table = normalise(block, mapping)

    assert table.column_names == CANONICAL_COLUMNS
    assert table.column("draught_m").to_pylist() == [None]
    assert table.column("vessel_name").to_pylist() == [None]
