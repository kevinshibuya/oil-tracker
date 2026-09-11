"""Running the state machine over a whole port and recording what it finds."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb

from oiltracker.events.detect import (
    arrival_plateau,
    departure_candidates,
    detect_departure,
    detect_visits,
    hours_between,
)
from oiltracker.events.model import Departure, Obs, Visit
from oiltracker.ports.config import PortConfig
from oiltracker.ports.store import current_terminals

TANKER_MMSIS = "select distinct mmsi from positions where port = %s order by mmsi"

OBSERVATIONS = """
select ts, st_y(geom), st_x(geom), sog, draught_m
from positions where port = %s and mmsi = %s order by ts
"""

VESSELS = """
select mmsi, imo, name, size_class, size_class_source, length_m, width_m,
       dwt_t, tpc_t_per_cm, scantling_draught_m, particulars_source
from vessels
"""

CLEAR_EVENTS = "delete from export_events where port = %s"
CLEAR_VISITS = "delete from berth_visits where port = %s"

INSERT_VISIT = """
insert into berth_visits (
    port, mmsi, terminal_id, polygon_version, entered_at, exited_at, dwell_hours,
    low_speed_fraction, observations, open_ended, code_version)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
returning id
"""

INSERT_EVENT = """
insert into export_events (
    berth_visit_id, port, mmsi, imo, name, size_class, size_class_source, length_m, width_m,
    dwt_t, tpc_t_per_cm, scantling_draught_m, particulars_source,
    terminal_id, polygon_version, entered_at, exited_at,
    gate_crossed_at, hours_to_gate, gate_interpolated,
    arrival_draught_m, arrival_draught_at, departure_draught_candidates, code_version)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s)
"""


@dataclass(frozen=True)
class VesselIdentity:
    """What the vessels table knows about one hull, copied onto each of its events."""

    imo: int | None
    name: str | None
    size_class: str
    size_class_source: str
    length_m: float | None
    width_m: float | None
    dwt_t: float | None
    tpc_t_per_cm: float | None
    scantling_draught_m: float | None
    particulars_source: str | None


def detect_port(
    conn: psycopg.Connection[TupleRow], cfg: PortConfig, code_version: str
) -> tuple[int, int]:
    """Rebuild this port's berth visits and export events. Returns both counts."""
    terminals = current_terminals(conn, cfg.id)
    if not terminals:
        raise ValueError(f"no terminals loaded for {cfg.id}: run oil port load first")

    vessels = _vessel_identities(conn)
    _ = conn.execute(CLEAR_EVENTS, (cfg.id,))
    _ = conn.execute(CLEAR_VISITS, (cfg.id,))

    visits_written = 0
    events_written = 0
    for (mmsi,) in conn.execute(TANKER_MMSIS, (cfg.id,)).fetchall():
        observations = _observations(conn, cfg.id, int(mmsi))
        visits = detect_visits(observations, terminals, cfg)

        for index, visit in enumerate(visits):
            next_entered_at = visits[index + 1].entered_at if index + 1 < len(visits) else None
            visit_id = _insert_visit(conn, cfg, int(mmsi), visit, code_version)
            visits_written += 1

            departure = detect_departure(observations, visit, next_entered_at, cfg)
            if departure is None:
                continue

            identity = vessels.get(int(mmsi))
            if identity is None:
                raise LookupError(
                    f"no vessels row for MMSI {mmsi}: run oil vessels build before detection"
                )
            _insert_event(
                conn,
                cfg,
                int(mmsi),
                visit,
                departure,
                identity,
                visit_id,
                observations,
                code_version,
            )
            events_written += 1

    return visits_written, events_written


def _vessel_identities(conn: psycopg.Connection[TupleRow]) -> dict[int, VesselIdentity]:
    identities: dict[int, VesselIdentity] = {}
    for row in conn.execute(VESSELS).fetchall():
        identities[int(row[0])] = VesselIdentity(*row[1:])
    return identities


def _observations(conn: psycopg.Connection[TupleRow], port_id: str, mmsi: int) -> list[Obs]:
    return [
        Obs(ts=ts, lat=float(lat), lon=float(lon), sog=sog, draught_m=draught)
        for ts, lat, lon, sog, draught in conn.execute(OBSERVATIONS, (port_id, mmsi)).fetchall()
    ]


def _insert_visit(
    conn: psycopg.Connection[TupleRow],
    cfg: PortConfig,
    mmsi: int,
    visit: Visit,
    code_version: str,
) -> int:
    row = conn.execute(
        INSERT_VISIT,
        (
            cfg.id,
            mmsi,
            visit.terminal_id,
            visit.polygon_version,
            visit.entered_at,
            visit.exited_at,
            visit.dwell_hours,
            visit.low_speed_fraction,
            visit.observations,
            visit.open_ended,
            code_version,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("inserting a berth visit returned no id")
    return int(row[0])


def _insert_event(
    conn: psycopg.Connection[TupleRow],
    cfg: PortConfig,
    mmsi: int,
    visit: Visit,
    departure: Departure,
    identity: VesselIdentity,
    visit_id: int,
    observations: Sequence[Obs],
    code_version: str,
) -> None:
    arrival = arrival_plateau(observations, visit, cfg)
    candidates = departure_candidates(observations, visit, cfg)

    _ = conn.execute(
        INSERT_EVENT,
        (
            visit_id,
            cfg.id,
            mmsi,
            identity.imo,
            identity.name,
            identity.size_class,
            identity.size_class_source,
            identity.length_m,
            identity.width_m,
            identity.dwt_t,
            identity.tpc_t_per_cm,
            identity.scantling_draught_m,
            identity.particulars_source,
            visit.terminal_id,
            visit.polygon_version,
            visit.entered_at,
            visit.exited_at,
            departure.gate_crossed_at,
            hours_between(visit.exited_at, departure.gate_crossed_at),
            departure.gate_interpolated,
            arrival.draught_m if arrival else None,
            arrival.at if arrival else None,
            Jsonb(
                [
                    {
                        "draught_m": candidate.draught_m,
                        "at": _iso(candidate.at),
                        "hours_after_exit": hours_between(visit.exited_at, candidate.at),
                        "observations": candidate.observations,
                    }
                    for candidate in candidates
                ]
            ),
            code_version,
        ),
    )


def _iso(moment: datetime) -> str:
    return moment.isoformat()
