# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false, reportMissingTypeArgument=false
# pyright: reportUnknownParameterType=false
#
# pyarrow's compute stubs erase element types; see oiltracker.ais.readers.
"""Loading a day's tanker positions and per-day statics into PostGIS.

Only tankers reach the database. Everything else stays in the day parquet, which
remains the complete record of what was in the box.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import psycopg
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from psycopg import sql
from psycopg.rows import TupleRow

from oiltracker.ais.backfill import parquet_path
from oiltracker.ais.schema import CANONICAL_COLUMNS, TANKER_CODE_MAX, TANKER_CODE_MIN
from oiltracker.ports.config import PortConfig

STAGING = """
drop table if exists staging_ais;
create temp table staging_ais (
    mmsi integer, ts timestamptz, lat double precision, lon double precision,
    sog real, cog real, heading real, vessel_name text, imo integer, call_sign text,
    vessel_type smallint, status smallint, length_m real, width_m real,
    draught_m real, cargo smallint, transceiver_class text
) on commit drop
"""

# Composed rather than interpolated: the column list is ours, but SQL built by
# string formatting is a habit worth not having.
COPY_STAGING = sql.SQL("copy staging_ais ({}) from stdin").format(
    sql.SQL(", ").join(sql.Identifier(name) for name in CANONICAL_COLUMNS)
)

CLEAR_POSITIONS = "delete from positions where port = %s and ts >= %s and ts < %s"
CLEAR_STATICS = "delete from vessel_statics_daily where port = %s and day = %s"

INSERT_POSITIONS = """
insert into positions (port, mmsi, ts, geom, sog, cog, heading, draught_m, status)
select %s, mmsi, ts, st_setsrid(st_makepoint(lon, lat), 4326), sog, cog, heading, draught_m, status
from staging_ais
"""

# Per field, the value of the latest observation of the day in which it is not
# null: a vessel that reports its name once at noon keeps that name for the day.
INSERT_STATICS = """
insert into vessel_statics_daily
    (port, mmsi, day, name, imo, call_sign, vessel_type, length_m, width_m, cargo,
     transceiver_class)
select %s, mmsi, %s,
    (array_agg(vessel_name order by ts desc) filter (where vessel_name is not null))[1],
    (array_agg(imo order by ts desc) filter (where imo is not null))[1],
    (array_agg(call_sign order by ts desc) filter (where call_sign is not null))[1],
    (array_agg(vessel_type order by ts desc) filter (where vessel_type is not null))[1],
    (array_agg(length_m order by ts desc) filter (where length_m is not null))[1],
    (array_agg(width_m order by ts desc) filter (where width_m is not null))[1],
    (array_agg(cargo order by ts desc) filter (where cargo is not null))[1],
    (array_agg(transceiver_class order by ts desc) filter (where transceiver_class is not null))[1]
from staging_ais
group by mmsi
"""

MARK_LOADED = "update ingest_manifest set loaded_at = now() where port = %s and day = %s"

LOADABLE_DAYS = """
select day from ingest_manifest
where port = %s and day between %s and %s and status = 'done'
  and (%s or loaded_at is null)
order by day
"""


def is_tanker(vessel_type: int | None, cargo: int | None) -> bool:
    """AIS codes 80 to 89, in either field. A null operand never matches."""
    return _in_range(vessel_type) or _in_range(cargo)


def _in_range(code: int | None) -> bool:
    return code is not None and TANKER_CODE_MIN <= code <= TANKER_CODE_MAX


def tanker_mask(table: pa.Table) -> pa.Array | pa.ChunkedArray:
    """`is_tanker` over a whole Arrow table.

    The or is Kleene, so a vessel with no VesselType but Cargo 80 still counts;
    rows where both are null come back null, and `filter` drops those.
    """
    return pc.or_kleene(
        _codes_in_range(table.column("vessel_type")),
        _codes_in_range(table.column("cargo")),
    )


def _codes_in_range(column: pa.ChunkedArray) -> pa.Array | pa.ChunkedArray:
    low = pa.scalar(TANKER_CODE_MIN, type=pa.int16())
    high = pa.scalar(TANKER_CODE_MAX, type=pa.int16())
    return pc.and_(pc.greater_equal(column, low), pc.less_equal(column, high))


def load_day(conn: psycopg.Connection[TupleRow], cfg: PortConfig, day: date, data_dir: Path) -> int:
    """Replace one day of this port's positions and statics. Returns rows loaded."""
    source = parquet_path(data_dir, cfg.id, day)
    table = pq.read_table(source)
    tankers = table.filter(tanker_mask(table))

    _ = conn.execute(STAGING)
    if tankers.num_rows:
        with conn.cursor().copy(COPY_STAGING) as copy:
            for batch in tankers.to_batches():
                for row in batch.to_pylist():
                    copy.write_row(tuple(row[name] for name in CANONICAL_COLUMNS))

    next_day = date.fromordinal(day.toordinal() + 1)
    _ = conn.execute(CLEAR_POSITIONS, (cfg.id, day, next_day))
    _ = conn.execute(CLEAR_STATICS, (cfg.id, day))
    _ = conn.execute(INSERT_POSITIONS, (cfg.id,))
    _ = conn.execute(INSERT_STATICS, (cfg.id, day))
    _ = conn.execute(MARK_LOADED, (cfg.id, day))
    return tankers.num_rows


def loadable_days(
    conn: psycopg.Connection[TupleRow], cfg: PortConfig, start: date, end: date, force: bool
) -> list[date]:
    """Days the manifest calls done and that are not already loaded."""
    rows = conn.execute(LOADABLE_DAYS, (cfg.id, start, end, force)).fetchall()
    return [row[0] for row in rows]
