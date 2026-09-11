"""Deriving terminal polygons from where tankers actually sit still.

A seed circle says roughly where a terminal is; the low-speed positions say
where its berths are. Everything here works in EPSG:32614 (UTM 14N), so buffers
and areas are in metres, and comes back as WGS84 at the end.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import psycopg
from psycopg.rows import TupleRow
from shapely import Polygon, from_wkt

from oiltracker.ports.config import PortConfig, Terminal, TerminalSeed, seed_circle

UTM = 32614

MIN_POINTS = 100
"""Below this a seed has too little evidence to derive anything from."""

CONCAVE_RATIO = 0.5
BUFFER_M = 100.0

Fallback = Literal["none", "convex", "seed"]

LOW_SPEED_POINTS = """
with pts as (
    select st_transform(geom, %(utm)s) as g
    from positions
    where port = %(port)s
      and sog is not null and sog < %(low_speed)s
      and st_dwithin(
          geom::geography,
          st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326)::geography,
          %(radius)s)
)
select
    count(*),
    st_astext(st_concavehull(st_collect(g), %(ratio)s, false)),
    st_astext(st_convexhull(st_collect(g)))
from pts
"""

BUFFERED = "select st_astext(st_buffer(st_geomfromtext(%s, %s), %s))"

# Voronoi cells of the seed centres, so shared water goes to the nearer terminal.
VORONOI = """
with centres as (
    select terminal_id,
           st_transform(st_setsrid(st_makepoint(lon, lat), 4326), %(utm)s) as c
    from unnest(%(ids)s::text[], %(lons)s::float8[], %(lats)s::float8[])
        as t(terminal_id, lon, lat)
), cells as (
    select (st_dump(st_voronoipolygons(st_collect(c)))).geom as cell from centres
)
select centres.terminal_id, st_astext(cells.cell)
from centres join cells on st_contains(cells.cell, centres.c)
"""

RESOLVE = """
select st_astext(
    st_difference(st_geomfromtext(%s, %s), st_intersection(
        st_intersection(st_geomfromtext(%s, %s), st_geomfromtext(%s, %s)),
        st_geomfromtext(%s, %s))))
"""

LARGEST_PART = """
select st_astext(geom) from (
    select (st_dump(st_geomfromtext(%s, %s))).geom as geom
) parts order by st_area(geom) desc limit 1
"""

TO_WGS84 = """
select st_astext(st_transform(st_geomfromtext(%s, %s), 4326)),
       st_area(st_geomfromtext(%s, %s))
"""


@dataclass(frozen=True)
class DerivedTerminal:
    """One derived polygon, with the evidence behind it."""

    terminal: Terminal
    points: int
    area_m2: float
    fallback: Fallback


def derive_terminals(
    conn: psycopg.Connection[TupleRow], cfg: PortConfig, version: int
) -> list[DerivedTerminal]:
    """Derive one polygon per configured terminal from the port's low-speed positions."""
    shapes: dict[str, str] = {}
    counts: dict[str, int] = {}
    fallbacks: dict[str, Fallback] = {}

    for seed in cfg.terminal_seeds:
        count, hull_wkt, fallback = _hull_for(conn, cfg, seed)
        counts[seed.terminal_id] = count
        fallbacks[seed.terminal_id] = fallback
        shapes[seed.terminal_id] = (
            hull_wkt if fallback == "seed" else _scalar(conn, BUFFERED, (hull_wkt, UTM, BUFFER_M))
        )

    shapes = _resolve_overlaps(conn, cfg, shapes)

    derived: list[DerivedTerminal] = []
    for seed in cfg.terminal_seeds:
        wkt_utm = shapes[seed.terminal_id]
        row = conn.execute(TO_WGS84, (wkt_utm, UTM, wkt_utm, UTM)).fetchone()
        assert row is not None
        geom = from_wkt(str(row[0]))
        if not isinstance(geom, Polygon):
            raise ValueError(f"{seed.terminal_id} derived to {type(geom).__name__}, not a polygon")
        derived.append(
            DerivedTerminal(
                terminal=Terminal(
                    terminal_id=seed.terminal_id,
                    name=seed.name,
                    crude=seed.crude,
                    version=version,
                    geom=geom,
                ),
                points=counts[seed.terminal_id],
                area_m2=float(row[1]),
                fallback=fallbacks[seed.terminal_id],
            )
        )
    return derived


def _hull_for(
    conn: psycopg.Connection[TupleRow], cfg: PortConfig, seed: TerminalSeed
) -> tuple[int, str, Fallback]:
    """The hull around a seed's low-speed points, and which rule produced it."""
    row = conn.execute(
        LOW_SPEED_POINTS,
        {
            "utm": UTM,
            "port": cfg.id,
            "low_speed": cfg.low_speed_knots,
            "lon": seed.centre[1],
            "lat": seed.centre[0],
            "radius": cfg.seed_radius_m,
            "ratio": CONCAVE_RATIO,
        },
    ).fetchone()
    assert row is not None
    count, concave_wkt, convex_wkt = int(row[0]), row[1], row[2]

    if count < MIN_POINTS:
        circle = seed_circle(seed.centre[0], seed.centre[1], cfg.seed_radius_m)
        return count, _to_utm(conn, circle.wkt), "seed"

    if _is_single_valid_polygon(conn, concave_wkt):
        return count, str(concave_wkt), "none"
    return count, str(convex_wkt), "convex"


def _resolve_overlaps(
    conn: psycopg.Connection[TupleRow], cfg: PortConfig, shapes: dict[str, str]
) -> dict[str, str]:
    """Give every square metre two terminals both claim to whichever centre is nearer."""
    cells = _voronoi_cells(conn, cfg)
    resolved = dict(shapes)

    ids = [seed.terminal_id for seed in cfg.terminal_seeds]
    for index, first in enumerate(ids):
        for second in ids[index + 1 :]:
            if not _overlaps(conn, resolved[first], resolved[second]):
                continue
            trimmed_first = _scalar(
                conn,
                RESOLVE,
                (
                    resolved[first],
                    UTM,
                    resolved[first],
                    UTM,
                    resolved[second],
                    UTM,
                    cells[second],
                    UTM,
                ),
            )
            trimmed_second = _scalar(
                conn,
                RESOLVE,
                (
                    resolved[second],
                    UTM,
                    resolved[first],
                    UTM,
                    resolved[second],
                    UTM,
                    cells[first],
                    UTM,
                ),
            )
            resolved[first] = _largest_part(conn, trimmed_first)
            resolved[second] = _largest_part(conn, trimmed_second)
    return resolved


def _voronoi_cells(conn: psycopg.Connection[TupleRow], cfg: PortConfig) -> dict[str, str]:
    rows = conn.execute(
        VORONOI,
        {
            "utm": UTM,
            "ids": [seed.terminal_id for seed in cfg.terminal_seeds],
            "lons": [seed.centre[1] for seed in cfg.terminal_seeds],
            "lats": [seed.centre[0] for seed in cfg.terminal_seeds],
        },
    ).fetchall()
    return {str(terminal_id): str(cell) for terminal_id, cell in rows}


def _overlaps(conn: psycopg.Connection[TupleRow], first: str, second: str) -> bool:
    row = conn.execute(
        "select st_area(st_intersection(st_geomfromtext(%s, %s), st_geomfromtext(%s, %s))) > 0",
        (first, UTM, second, UTM),
    ).fetchone()
    assert row is not None
    return bool(row[0])


def _is_single_valid_polygon(conn: psycopg.Connection[TupleRow], wkt: str | None) -> bool:
    if wkt is None:
        return False
    row = conn.execute(
        "select st_geometrytype(g) = 'ST_Polygon' and st_isvalid(g)"
        " from (select st_geomfromtext(%s, %s) as g) t",
        (wkt, UTM),
    ).fetchone()
    return row is not None and bool(row[0])


def _largest_part(conn: psycopg.Connection[TupleRow], wkt: str) -> str:
    return _scalar(conn, LARGEST_PART, (wkt, UTM))


def _to_utm(conn: psycopg.Connection[TupleRow], wkt_4326: str) -> str:
    return _scalar(
        conn, "select st_astext(st_transform(st_geomfromtext(%s, 4326), %s))", (wkt_4326, UTM)
    )


def _scalar(conn: psycopg.Connection[TupleRow], statement: str, params: Sequence[object]) -> str:
    row = conn.execute(statement, params).fetchone()  # pyright: ignore[reportArgumentType]  # module-level literal SQL
    assert row is not None
    return str(row[0])
