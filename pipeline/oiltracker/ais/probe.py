"""Finding which archives actually serve a year, and reading their headers.

Nothing national touches disk: each archive is probed with two HEAD requests and
then read only as far as its first line of CSV.
"""

import io
from collections.abc import Iterable, Iterator, Sequence
from datetime import date

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import zstandard
from stream_unzip import stream_unzip

from oiltracker.ais.sources import SourceFormat, SourceSpec, url_for

PREFIX_BYTES = 256 * 1024
"""How far into an archive the header search is allowed to read."""

FOOTER_BYTES = 256 * 1024
"""How much of a parquet tail to fetch when reading its schema by range."""


def probe_year(
    year: int,
    candidates: Sequence[tuple[str, SourceFormat]],
    client: httpx.Client,
) -> list[SourceSpec]:
    """Every candidate that serves both 1 January and 15 July of `year`, in order.

    A candidate is kept only when both HEAD requests return 200; its header is
    then read verbatim from 1 January.
    """
    kept: list[SourceSpec] = []
    for pattern, fmt in candidates:
        probe = SourceSpec(url_pattern=pattern, format=fmt, header=[])
        first = url_for(probe, date(year, 1, 1))
        middle = url_for(probe, date(year, 7, 15))
        if not (_head_ok(client, first) and _head_ok(client, middle)):
            continue
        kept.append(
            SourceSpec(url_pattern=pattern, format=fmt, header=read_header(client, first, fmt))
        )
    return kept


def read_header(client: httpx.Client, url: str, fmt: SourceFormat) -> list[str]:
    """The column names of one archive, however it is packaged."""
    if fmt == "csv_zst":
        return _header_from_zst(_stream_prefix(client, url, PREFIX_BYTES))
    if fmt == "csv_zip":
        return _header_from_zip(_stream_prefix(client, url, PREFIX_BYTES))
    return _header_from_parquet(client, url)


def _head_ok(client: httpx.Client, url: str) -> bool:
    try:
        response = client.head(url, follow_redirects=True)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _stream_prefix(client: httpx.Client, url: str, max_bytes: int) -> Iterator[bytes]:
    """Yield the first `max_bytes` of a URL, then close the connection."""
    with client.stream("GET", url, follow_redirects=True) as response:
        _ = response.raise_for_status()
        seen = 0
        for chunk in response.iter_bytes():
            yield chunk
            seen += len(chunk)
            if seen >= max_bytes:
                return


def _split_header(buffer: bytes) -> list[str]:
    if b"\n" not in buffer:
        raise ValueError(f"no line break in the first {PREFIX_BYTES} bytes")
    line = buffer.split(b"\n", 1)[0]
    return [name.strip() for name in line.decode("utf-8-sig").strip().split(",")]


def _header_from_zst(chunks: Iterable[bytes]) -> list[str]:
    decompressor = zstandard.ZstdDecompressor().decompressobj()
    buffer = b""
    for chunk in chunks:
        buffer += decompressor.decompress(chunk)
        if b"\n" in buffer:
            break
    return _split_header(buffer)


def _header_from_zip(chunks: Iterable[bytes]) -> list[str]:
    for _name, _size, entry in stream_unzip(chunks):
        buffer = b""
        for chunk in entry:
            buffer += chunk
            if b"\n" in buffer:
                break
        return _split_header(buffer)
    raise ValueError("the zip archive held no entries")


def _header_from_parquet(client: httpx.Client, url: str) -> list[str]:
    """Read a parquet schema from its tail, without fetching the whole file."""
    head = client.head(url, follow_redirects=True)
    _ = head.raise_for_status()
    size = int(head.headers["content-length"])
    with _ParquetTail(client, url, size) as tail:
        # PythonFile is pyarrow's adapter from a seekable Python object to a NativeFile.
        return list(pq.ParquetFile(pa.PythonFile(tail, mode="r")).schema_arrow.names)


class _ParquetTail(io.RawIOBase):
    """A read-only, seekable view of a remote parquet file.

    Only the tail is ever fetched: parquet keeps its schema in the footer, so a
    single range request covers every read the schema parser makes.
    """

    def __init__(self, client: httpx.Client, url: str, size: int) -> None:
        super().__init__()
        self._size = size
        self._offset = 0
        self._start = max(0, size - FOOTER_BYTES)
        response = client.get(
            url, headers={"Range": f"bytes={self._start}-{size - 1}"}, follow_redirects=True
        )
        _ = response.raise_for_status()
        self._tail = response.content

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._offset

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._offset = offset
        elif whence == io.SEEK_CUR:
            self._offset += offset
        else:
            self._offset = self._size + offset
        return self._offset

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]  # RawIOBase types this as a generic writable buffer
        wanted = min(len(buffer), self._size - self._offset)
        if wanted <= 0:
            return 0
        if self._offset < self._start:
            raise ValueError("parquet schema read reached beyond the fetched tail")
        begin = self._offset - self._start
        buffer[:wanted] = self._tail[begin : begin + wanted]
        self._offset += wanted
        return wanted
