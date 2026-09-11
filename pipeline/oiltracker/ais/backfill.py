"""Pulling a span of days into one parquet each, with the manifest as the record.

The manifest is written before the work starts and again when it ends, so a run
killed halfway leaves `running` rows that the next run picks up rather than a
silent hole.
"""

import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
import psycopg
import pyarrow.parquet as pq
from psycopg.rows import TupleRow

from oiltracker.ais.readers import StreamStats, iter_chunks
from oiltracker.ais.schema import PARQUET_SCHEMA
from oiltracker.ais.sources import SourceSpec, url_for
from oiltracker.db.connect import connect
from oiltracker.ports.config import PortConfig
from oiltracker.settings import Settings

Status = Literal["running", "done", "failed", "missing"]

STALE_RUNNING = timedelta(hours=6)
"""A `running` row older than this belonged to a run that died."""

DAY_TIMEOUT_S = 1800.0

START = """
insert into ingest_manifest (port, day, source_url, format, status, started_at)
values (%s, %s, %s, %s, 'running', now())
on conflict (port, day) do update set
  source_url = excluded.source_url, format = excluded.format,
  status = 'running', started_at = now(), error = null, finished_at = null
"""

FINISH = """
update ingest_manifest
set status = %s, bytes = %s, rows_kept = %s, error = %s, finished_at = now()
where port = %s and day = %s
"""

ROW = """
select port, day, source_url, format, bytes, rows_kept, status, error,
       started_at, finished_at, loaded_at
from ingest_manifest where port = %s and day = %s
"""

SURVEY = "select day, status, started_at from ingest_manifest where port = %s"


@dataclass(frozen=True)
class ManifestRow:
    """One day's ingest record, mirroring `ingest_manifest`."""

    port: str
    day: date
    source_url: str | None
    format: str | None
    bytes: int | None
    rows_kept: int | None
    status: Status
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    loaded_at: datetime | None


def parquet_path(data_dir: Path, port_id: str, day: date) -> Path:
    return data_dir / "ais" / port_id / f"{day.isoformat()}.parquet"


def backfill_day(
    conn: psycopg.Connection[TupleRow],
    cfg: PortConfig,
    specs: Sequence[SourceSpec],
    day: date,
    data_dir: Path,
    client: httpx.Client | None = None,
) -> ManifestRow:
    """Fetch one day into `<data_dir>/ais/<port>/<day>.parquet`.

    Specs are tried in order. A 404 moves to the next; when every spec 404s the
    day is `missing`, which counts as complete — some days were never published.
    Any other failure is `failed` and re-raised, after the manifest is written.
    """
    if not specs:
        raise ValueError(f"no source for {day.year}")

    owned = client is None
    http = client or httpx.Client(timeout=DAY_TIMEOUT_S, follow_redirects=True)
    target = parquet_path(data_dir, cfg.id, day)
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        last_url = ""
        for spec in specs:
            last_url = url_for(spec, day)
            _ = conn.execute(START, (cfg.id, day, last_url, spec.format))
            conn.commit()
            try:
                written, stats = _write_day(http, spec, last_url, cfg, day, target)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                _finish(conn, cfg.id, day, "failed", error=str(exc))
                raise
            except Exception as exc:
                _finish(conn, cfg.id, day, "failed", error=f"{type(exc).__name__}: {exc}")
                raise
            _finish(conn, cfg.id, day, "done", nbytes=stats.bytes_read, rows=written)
            return read_manifest(conn, cfg.id, day)

        _finish(conn, cfg.id, day, "missing", error=f"every source returned 404 (last {last_url})")
        return read_manifest(conn, cfg.id, day)
    finally:
        if owned:
            http.close()


def _write_day(
    client: httpx.Client,
    spec: SourceSpec,
    url: str,
    cfg: PortConfig,
    day: date,
    target: Path,
) -> tuple[int, StreamStats]:
    """Stream one archive into a temporary parquet, then move it into place."""
    stats = StreamStats()
    temporary = target.with_suffix(".parquet.tmp")
    written = 0
    try:
        with pq.ParquetWriter(temporary, PARQUET_SCHEMA) as writer:
            for block in iter_chunks(url, spec, client, cfg.bbox, day.year, stats):
                if block.num_rows:
                    writer.write_table(block)
                    written += block.num_rows
            if written == 0:
                # An empty day is still a real answer; keep the schema on disk.
                writer.write_table(PARQUET_SCHEMA.empty_table())
        _ = temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return written, stats


def _finish(
    conn: psycopg.Connection[TupleRow],
    port_id: str,
    day: date,
    status: Status,
    nbytes: int | None = None,
    rows: int | None = None,
    error: str | None = None,
) -> None:
    _ = conn.execute(FINISH, (status, nbytes, rows, error, port_id, day))
    conn.commit()


def read_manifest(conn: psycopg.Connection[TupleRow], port_id: str, day: date) -> ManifestRow:
    row = conn.execute(ROW, (port_id, day)).fetchone()
    if row is None:
        raise LookupError(f"no manifest row for {port_id} {day}")
    return ManifestRow(*row)


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def pending_days(
    conn: psycopg.Connection[TupleRow], cfg: PortConfig, days: Sequence[date], data_dir: Path
) -> list[date]:
    """The days still to fetch: everything but a `done` day with its parquet, or a `missing` one."""
    survey = {
        day: (status, started_at)
        for day, status, started_at in conn.execute(SURVEY, (cfg.id,)).fetchall()
    }
    now = datetime.now(UTC)

    pending: list[date] = []
    for day in days:
        seen = survey.get(day)
        if seen is None:
            pending.append(day)
            continue
        status, started_at = seen
        if status == "missing":
            continue
        if status == "done" and parquet_path(data_dir, cfg.id, day).exists():
            continue
        if status == "running" and now - started_at < STALE_RUNNING:
            continue
        pending.append(day)
    return pending


def backfill(
    settings: Settings,
    cfg: PortConfig,
    sources: dict[int, list[SourceSpec]],
    start: date,
    end: date,
    data_dir: Path,
    workers: int = 2,
) -> dict[str, int]:
    """Fetch every day in the span that is not already accounted for.

    A day that fails is logged and left `failed`; the run carries on, so one bad
    day never costs the other thousand.
    """
    with connect(settings) as conn:
        todo = pending_days(conn, cfg, days_between(start, end), data_dir)

    counts: dict[str, int] = {}
    lock = threading.Lock()

    def run(slice_of_days: list[date]) -> None:
        with (
            connect(settings) as conn,
            httpx.Client(timeout=DAY_TIMEOUT_S, follow_redirects=True) as client,
        ):
            for day in slice_of_days:
                try:
                    row = backfill_day(conn, cfg, sources.get(day.year, []), day, data_dir, client)
                    status, detail = row.status, f"{row.rows_kept} rows"
                except Exception as exc:
                    conn.rollback()
                    status, detail = "failed", f"{type(exc).__name__}: {exc}"
                with lock:
                    counts[status] = counts.get(status, 0) + 1
                    done = sum(counts.values())
                print(f"[{done}/{len(todo)}] {day} {status} {detail}", file=sys.stderr, flush=True)

    slices = [todo[index::workers] for index in range(workers)]
    threads = [threading.Thread(target=run, args=(slice_of_days,)) for slice_of_days in slices]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return counts
