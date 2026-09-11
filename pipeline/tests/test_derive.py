"""Deriving polygons from synthetic low-speed positions."""

import math
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import TupleRow
from shapely import Point

from oiltracker.ports.config import M_PER_DEGREE_LAT, PortConfig, load_port, seed_circle
from oiltracker.ports.derive import MIN_POINTS, derive_terminals

VERSION = 1
START = datetime(2024, 3, 5, tzinfo=UTC)

INSERT = """
insert into positions (port, mmsi, ts, geom, sog)
values (%s, %s, %s, st_setsrid(st_makepoint(%s, %s), 4326), %s)
"""


@pytest.fixture
def cfg() -> PortConfig:
    return load_port("corpus_christi")


def centre(cfg: PortConfig, terminal_id: str) -> tuple[float, float]:
    return next(s.centre for s in cfg.terminal_seeds if s.terminal_id == terminal_id)


def offset(point: tuple[float, float], east_m: float, north_m: float) -> tuple[float, float]:
    lat = point[0] + north_m / M_PER_DEGREE_LAT
    lon = point[1] + east_m / (M_PER_DEGREE_LAT * math.cos(math.radians(point[0])))
    return lat, lon


def scatter(
    conn: psycopg.Connection[TupleRow],
    cfg: PortConfig,
    at: tuple[float, float],
    count: int,
    sog: float,
    mmsi: int,
) -> None:
    """A small cluster of observations around a point."""
    for index in range(count):
        spread_m = 40.0
        lat, lon = offset(
            at,
            spread_m * math.cos(index * 2.39996),
            spread_m * math.sin(index * 2.39996),
        )
        _ = conn.execute(INSERT, (cfg.id, mmsi, START + timedelta(minutes=index), lon, lat, sog))


def test_two_clusters_split_along_the_voronoi_boundary(
    db: psycopg.Connection[TupleRow], cfg: PortConfig
) -> None:
    """Two berths 200 m apart either side of the Enbridge / STG boundary."""
    enbridge = centre(cfg, "enbridge_ingleside")
    gateway = centre(cfg, "south_texas_gateway")
    midpoint = ((enbridge[0] + gateway[0]) / 2, (enbridge[1] + gateway[1]) / 2)

    # Straddling the boundary: one cluster 100 m each side of the midpoint.
    scatter(db, cfg, offset(midpoint, 0, -100), 300, 0.1, mmsi=367000001)
    scatter(db, cfg, offset(midpoint, 0, 100), 300, 0.1, mmsi=367000002)
    # Fast traffic must not count.
    scatter(db, cfg, midpoint, 50, 9.0, mmsi=367000003)

    derived = {one.terminal.terminal_id: one for one in derive_terminals(db, cfg, VERSION)}

    enbridge_poly = derived["enbridge_ingleside"].terminal.geom
    gateway_poly = derived["south_texas_gateway"].terminal.geom
    assert enbridge_poly.intersection(gateway_poly).area == pytest.approx(0.0, abs=1e-12)

    assert derived["enbridge_ingleside"].points >= MIN_POINTS
    assert derived["south_texas_gateway"].points >= MIN_POINTS
    for one in derived.values():
        assert one.terminal.version == VERSION


def test_a_seed_with_too_little_evidence_falls_back_to_its_circle(
    db: psycopg.Connection[TupleRow], cfg: PortConfig
) -> None:
    scatter(db, cfg, centre(cfg, "flint_hills_ingleside"), 10, 0.1, mmsi=367000004)

    derived = {one.terminal.terminal_id: one for one in derive_terminals(db, cfg, VERSION)}

    flint_hills = derived["flint_hills_ingleside"]
    assert flint_hills.points == 10
    assert flint_hills.fallback == "seed"
    # Rule 2 gives it the seed circle, and rule 5 then trims whatever it shares
    # with South Texas Gateway, whose circle genuinely overlaps this one by about
    # 416 m. So it is the circle less that shared water, and no more.
    circle = seed_circle(*centre(cfg, "flint_hills_ingleside"), cfg.seed_radius_m)
    derived_geom = flint_hills.terminal.geom
    assert derived_geom.difference(circle.buffer(1e-9)).area == pytest.approx(0.0, abs=1e-12)
    assert derived_geom.covers(
        Point(centre(cfg, "flint_hills_ingleside")[1], centre(cfg, "flint_hills_ingleside")[0])
    )
    assert 0.5 < derived_geom.area / circle.area < 1.0

    # Whatever the fallbacks, the version never contains two overlapping polygons.
    polygons = [one.terminal.geom for one in derived.values()]
    for index, first in enumerate(polygons):
        for second in polygons[index + 1 :]:
            assert first.intersection(second).area == pytest.approx(0.0, abs=1e-12)

    # The two seeds with nothing at all also fall back.
    assert derived["enbridge_ingleside"].fallback == "seed"
    assert derived["enbridge_ingleside"].points == 0


def test_fast_positions_are_not_evidence_of_a_berth(
    db: psycopg.Connection[TupleRow], cfg: PortConfig
) -> None:
    scatter(db, cfg, centre(cfg, "enbridge_ingleside"), 400, 9.0, mmsi=367000005)

    derived = {one.terminal.terminal_id: one for one in derive_terminals(db, cfg, VERSION)}

    assert derived["enbridge_ingleside"].points == 0
    assert derived["enbridge_ingleside"].fallback == "seed"
