# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportMissingTypeArgument=false
#
# pyarrow's compute stubs erase the element type of everything they return, so
# strict mode cannot follow a ChunkedArray through pc.if_else and friends. The
# imprecision is the stubs', not ours: this module is the only place pyarrow's
# untyped compute layer is touched, and every function here has a precise
# signature in pa.Table terms. Genuine type errors stay switched on.
"""Streaming a day of national AIS into bounding-box-filtered Arrow tables.

Nothing national touches disk. The archive is pulled over HTTP, decompressed in
flight, parsed in blocks, and each block is cut down to the port's bounding box
before the next one is read, so peak memory is one block rather than one day.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO

import httpx
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pa_csv
import zstandard
from stream_unzip import stream_unzip

from oiltracker.ais.schema import (
    CANONICAL_COLUMNS,
    COG_NOT_REPORTED,
    CSV_TYPES,
    HEADING_NOT_REPORTED,
    PARQUET_SCHEMA,
    SOG_NOT_REPORTED,
    canonical_for,
)
from oiltracker.ais.sources import SourceSpec
from oiltracker.ports.config import BBox

BLOCK_ROWS = 500_000
BLOCK_BYTES = 64 << 20
"""CSV parse block. Roughly BLOCK_ROWS of AIS at about 130 bytes a row."""

HTTP_CHUNK = 1 << 20


@dataclass
class StreamStats:
    """How much compressed data a day's read actually pulled."""

    bytes_read: int = field(default=0)


def iter_chunks(
    url: str,
    spec: SourceSpec,
    client: httpx.Client,
    bbox: BBox,
    year: int,
    stats: StreamStats | None = None,
) -> Iterator[pa.Table]:
    """Yield the rows of one archived day that fall inside `bbox`, block by block.

    Each table matches PARQUET_SCHEMA exactly, so blocks concatenate and write
    without further conversion.
    """
    if spec.format == "parquet":
        raise NotImplementedError("parquet source not selected by the probe")

    mapping = canonical_for(spec.header, year)
    with client.stream("GET", url, follow_redirects=True) as response:
        _ = response.raise_for_status()
        raw = _CountingChunks(response.iter_bytes(HTTP_CHUNK), stats)
        text = _decompress(raw, spec)
        reader = pa_csv.open_csv(
            text,
            read_options=pa_csv.ReadOptions(block_size=BLOCK_BYTES),
            convert_options=pa_csv.ConvertOptions(
                column_types={source: CSV_TYPES[canonical] for source, canonical in mapping.items()}
            ),
        )
        for batch in reader:
            block = normalise(pa.Table.from_batches([batch]), mapping)
            yield filter_bbox(block, bbox)


def normalise(block: pa.Table, mapping: dict[str, str]) -> pa.Table:
    """Rename to canonical columns, null the sentinels, and match the schema."""
    renamed = block.select(list(mapping.keys())).rename_columns(list(mapping.values()))

    columns: dict[str, pa.ChunkedArray] = {}
    for name in CANONICAL_COLUMNS:
        if name in renamed.column_names:
            columns[name] = renamed.column(name)
        else:
            field_type = PARQUET_SCHEMA.field(name).type
            columns[name] = pa.chunked_array([pa.nulls(renamed.num_rows, field_type)])

    columns["imo"] = _imo_to_int(columns["imo"])
    columns["ts"] = columns["ts"].cast(pa.timestamp("us", tz="UTC"))

    # "Not reported" codes, per field, become null rather than a plausible number.
    for name in ("draught_m", "length_m", "width_m"):
        columns[name] = _null_where(columns[name], pc.less_equal(columns[name], _f32(0.0)))
    columns["sog"] = _null_where(
        columns["sog"], pc.greater_equal(columns["sog"], _f32(SOG_NOT_REPORTED))
    )
    columns["cog"] = _null_where(
        columns["cog"], pc.greater_equal(columns["cog"], _f32(COG_NOT_REPORTED))
    )
    columns["heading"] = _null_where(
        columns["heading"], pc.equal(columns["heading"], _f32(HEADING_NOT_REPORTED))
    )

    return pa.Table.from_arrays(
        [columns[name].combine_chunks() for name in CANONICAL_COLUMNS], schema=PARQUET_SCHEMA
    )


def filter_bbox(block: pa.Table, bbox: BBox) -> pa.Table:
    """Only the rows inside the port's bounding box."""
    inside = pc.and_(
        pc.and_(
            pc.greater_equal(block.column("lat"), _f64(bbox.lat_min)),
            pc.less_equal(block.column("lat"), _f64(bbox.lat_max)),
        ),
        pc.and_(
            pc.greater_equal(block.column("lon"), _f64(bbox.lon_min)),
            pc.less_equal(block.column("lon"), _f64(bbox.lon_max)),
        ),
    )
    # filter drops null mask entries by default, so a row without coordinates
    # falls out rather than sneaking through.
    return block.filter(inside)


def _f32(value: float) -> pa.Scalar[pa.DataType]:
    return pa.scalar(value, type=pa.float32())


def _f64(value: float) -> pa.Scalar[pa.DataType]:
    return pa.scalar(value, type=pa.float64())


def _null_where(column: pa.ChunkedArray, condition: pa.Array | pa.ChunkedArray) -> pa.ChunkedArray:
    """Blank out every element the condition marks, leaving the rest alone."""
    blanked = pc.if_else(condition, pa.scalar(None, type=column.type), column)
    return pa.chunked_array([blanked]) if isinstance(blanked, pa.Array) else blanked


def _imo_to_int(column: pa.ChunkedArray) -> pa.ChunkedArray:
    """`IMO9999999` and `9999999` both become 9999999; anything else is null."""
    if not pa.types.is_string(column.type):
        return column.cast(pa.int32())
    stripped = pc.replace_substring_regex(column, r"^\s*(?i:IMO)\s*", "")
    digits = pc.if_else(
        pc.match_substring_regex(stripped, r"^\d+$"), stripped, pa.scalar(None, type=pa.string())
    )
    return pa.chunked_array([digits.cast(pa.int32())])


def _decompress(raw: Iterator[bytes], spec: SourceSpec) -> IO[bytes]:
    """The archive's CSV bytes, whatever it was packed in."""
    if spec.format == "csv_zst":
        return io.BufferedReader(
            zstandard.ZstdDecompressor().stream_reader(_buffered(_ChunkReader(raw)))
        )
    return _buffered(_ChunkReader(_first_zip_entry(raw)))


def _buffered(raw: io.RawIOBase) -> io.BufferedReader:
    """A BufferedReader is both an IO[bytes] and a faster way to read small reads."""
    return io.BufferedReader(raw)


def _first_zip_entry(raw: Iterator[bytes]) -> Iterator[bytes]:
    """The bytes of the archive's first member, which is the day's CSV."""
    for _name, _size, entry in stream_unzip(raw):
        yield from entry
        return
    raise ValueError("the zip archive held no entries")


class _CountingChunks(Iterator[bytes]):
    """Passes chunks through, recording how many bytes crossed the wire."""

    def __init__(self, chunks: Iterator[bytes], stats: StreamStats | None) -> None:
        self._chunks = chunks
        self._stats = stats

    def __next__(self) -> bytes:
        chunk = next(self._chunks)
        if self._stats is not None:
            self._stats.bytes_read += len(chunk)
        return chunk


class _ChunkReader(io.RawIOBase):
    """A readable stream over an iterator of byte chunks."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        super().__init__()
        self._chunks = chunks
        self._pending = b""

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]  # RawIOBase declares a generic writable buffer
        while not self._pending:
            try:
                self._pending = next(self._chunks)
            except StopIteration:
                return 0
        take = min(len(buffer), len(self._pending))
        buffer[:take] = self._pending[:take]
        self._pending = self._pending[take:]
        return take
