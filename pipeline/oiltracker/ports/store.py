"""Reading and writing terminal polygons in PostGIS."""

from collections.abc import Sequence

import psycopg
from psycopg.rows import TupleRow
from shapely import Polygon, from_wkt

from oiltracker.ports.config import PortConfig, Terminal

UPSERT = """
insert into terminals (port, terminal_id, version, name, crude, geom)
values (%s, %s, %s, %s, %s, st_geomfromtext(%s, 4326))
on conflict (port, terminal_id, version)
do update set name = excluded.name, crude = excluded.crude, geom = excluded.geom
"""

CURRENT = """
select terminal_id, name, crude, version, st_astext(geom)
from terminals
where port = %s and version = (select max(version) from terminals where port = %s)
order by terminal_id
"""


def upsert_terminals(
    conn: psycopg.Connection[TupleRow], cfg: PortConfig, terminals: Sequence[Terminal]
) -> int:
    """Write each polygon at its own version, replacing any row already there."""
    for terminal in terminals:
        _ = conn.execute(
            UPSERT,
            (
                cfg.id,
                terminal.terminal_id,
                terminal.version,
                terminal.name,
                terminal.crude,
                terminal.geom.wkt,
            ),
        )
    return len(terminals)


def current_terminals(conn: psycopg.Connection[TupleRow], port_id: str) -> list[Terminal]:
    """The port's polygons at its highest version, terminal id ascending."""
    rows = conn.execute(CURRENT, (port_id, port_id)).fetchall()
    terminals: list[Terminal] = []
    for terminal_id, name, crude, version, wkt in rows:
        geom = from_wkt(str(wkt))
        if not isinstance(geom, Polygon):
            raise ValueError(f"{port_id}/{terminal_id} at version {version} is not a polygon")
        terminals.append(
            Terminal(
                terminal_id=str(terminal_id),
                name=str(name),
                crude=crude,
                version=int(version),
                geom=geom,
            )
        )
    return terminals
