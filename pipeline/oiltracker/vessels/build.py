"""Rebuilding the vessels table from what the day statics and positions say.

Every field is taken from the latest day that reported it, so a vessel that
broadcast its name once keeps it. Curated particulars then override the
estimates for the hulls we have looked up in a class register.
"""

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import TupleRow

from oiltracker.vessels.classify import Particulars, SizeClass, estimate_particulars, size_class

# Per MMSI: each static field from the latest day that reported it, joined to the
# first and last time the vessel was seen anywhere in the box.
SURVEY = """
with statics as (
    select mmsi,
        (array_agg(name order by day desc) filter (where name is not null))[1] as name,
        (array_agg(imo order by day desc) filter (where imo is not null))[1] as imo,
        (array_agg(call_sign order by day desc) filter (where call_sign is not null))[1]
            as call_sign,
        (array_agg(vessel_type order by day desc) filter (where vessel_type is not null))[1]
            as vessel_type,
        (array_agg(length_m order by day desc) filter (where length_m is not null))[1] as length_m,
        (array_agg(width_m order by day desc) filter (where width_m is not null))[1] as width_m
    from vessel_statics_daily where port = %s group by mmsi
), seen as (
    select mmsi, min(ts) as first_seen, max(ts) as last_seen
    from positions where port = %s group by mmsi
)
select s.mmsi, s.name, s.imo, s.call_sign, s.vessel_type, s.length_m, s.width_m,
       seen.first_seen, seen.last_seen
from statics s join seen on seen.mmsi = s.mmsi
order by s.mmsi
"""

CLEAR = "delete from vessels"

INSERT = """
insert into vessels (
    mmsi, imo, name, call_sign, vessel_type, length_m, width_m, first_seen, last_seen,
    size_class, size_class_source, dwt_t, tpc_t_per_cm, scantling_draught_m,
    particulars_source, code_version)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

CURATED_COLUMNS = [
    "imo",
    "mmsi",
    "name",
    "size_class",
    "dwt_t",
    "tpc_t_per_cm",
    "scantling_draught_m",
    "source_url",
]


@dataclass(frozen=True)
class CuratedVessel:
    """One hull looked up in a class register, overriding what the box estimate says."""

    mmsi: int
    imo: int | None
    name: str | None
    size_class: SizeClass
    dwt_t: float | None
    tpc_t_per_cm: float | None
    scantling_draught_m: float | None
    source_url: str


def read_curated(path: Path) -> dict[int, CuratedVessel]:
    """The curated particulars CSV, by MMSI. A header-only file is a valid empty answer."""
    if not path.exists():
        return {}
    with path.open(newline="") as handle:
        return {row.mmsi: row for row in _curated_rows(csv.DictReader(handle), path)}


def _curated_rows(reader: csv.DictReader[str], path: Path) -> Iterator[CuratedVessel]:
    if reader.fieldnames is not None and list(reader.fieldnames) != CURATED_COLUMNS:
        raise ValueError(f"{path} columns are {reader.fieldnames}, expected {CURATED_COLUMNS}")
    for row in reader:
        yield CuratedVessel(
            mmsi=int(row["mmsi"]),
            imo=_optional_int(row["imo"]),
            name=row["name"] or None,
            size_class=_as_size_class(row["size_class"], path),
            dwt_t=_optional_float(row["dwt_t"]),
            tpc_t_per_cm=_optional_float(row["tpc_t_per_cm"]),
            scantling_draught_m=_optional_float(row["scantling_draught_m"]),
            source_url=row["source_url"],
        )


def _as_size_class(value: str, path: Path) -> SizeClass:
    allowed: tuple[SizeClass, ...] = ("mr", "panamax_lr1", "aframax", "suezmax", "vlcc", "unknown")
    for candidate in allowed:
        if value == candidate:
            return candidate
    raise ValueError(f"{path} has size_class {value!r}, expected one of {allowed}")


def _optional_int(value: str) -> int | None:
    return int(value) if value.strip() else None


def _optional_float(value: str) -> float | None:
    return float(value) if value.strip() else None


def build_vessels(
    conn: psycopg.Connection[TupleRow], particulars_csv: Path, code_version: str, port_id: str
) -> int:
    """Rebuild the vessels table from scratch. Returns the number of rows written."""
    curated = read_curated(particulars_csv)
    rows = conn.execute(SURVEY, (port_id, port_id)).fetchall()
    _ = conn.execute(CLEAR)

    written = 0
    for mmsi, name, imo, call_sign, vessel_type, length_m, width_m, first_seen, last_seen in rows:
        _ = conn.execute(
            INSERT,
            _vessel_values(
                int(mmsi),
                name,
                imo,
                call_sign,
                vessel_type,
                length_m,
                width_m,
                first_seen,
                last_seen,
                curated.get(int(mmsi)),
                code_version,
            ),
        )
        written += 1
    return written


def _vessel_values(
    mmsi: int,
    name: str | None,
    imo: int | None,
    call_sign: str | None,
    vessel_type: int | None,
    length_m: float | None,
    width_m: float | None,
    first_seen: datetime,
    last_seen: datetime,
    curated: CuratedVessel | None,
    code_version: str,
) -> tuple[object, ...]:
    """One vessels row: the box estimate, overridden field by field where curated."""
    estimated_class = size_class(length_m, width_m)
    estimate = estimate_particulars(length_m, width_m, estimated_class)

    if curated is None:
        return (
            mmsi,
            imo,
            name,
            call_sign,
            vessel_type,
            length_m,
            width_m,
            first_seen,
            last_seen,
            estimated_class,
            "estimated",
            *_particulars_columns(estimate),
            code_version,
        )

    return (
        mmsi,
        curated.imo if curated.imo is not None else imo,
        curated.name or name,
        call_sign,
        vessel_type,
        length_m,
        width_m,
        first_seen,
        last_seen,
        curated.size_class,
        "curated",
        curated.dwt_t,
        curated.tpc_t_per_cm,
        curated.scantling_draught_m,
        "curated",
        code_version,
    )


def _particulars_columns(estimate: Particulars | None) -> tuple[object, ...]:
    if estimate is None:
        return (None, None, None, None)
    return (
        estimate.dwt_t,
        estimate.tpc_t_per_cm,
        estimate.scantling_draught_m,
        estimate.source,
    )
