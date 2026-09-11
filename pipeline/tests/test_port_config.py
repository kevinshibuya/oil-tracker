"""The real port configuration, and what `check_port` reports on broken versions."""

import math

from shapely import Polygon, box

from oiltracker.ports.check import check_port, gate_offset_m, metres_between
from oiltracker.ports.config import (
    M_PER_DEGREE_LAT,
    PortConfig,
    Terminal,
    load_port,
    read_terminals_geojson,
    seed_circle,
    terminals_path,
)

PORT_ID = "corpus_christi"
SEED_IDS = ["enbridge_ingleside", "flint_hills_ingleside", "south_texas_gateway"]


def square(lat: float, lon: float, half_m: float) -> Polygon:
    """An axis-aligned square of side `2 * half_m` around a point."""
    d_lat = half_m / M_PER_DEGREE_LAT
    d_lon = half_m / (M_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return box(lon - d_lon, lat - d_lat, lon + d_lon, lat + d_lat)


def centre_of(cfg: PortConfig, terminal_id: str) -> tuple[float, float]:
    return next(s.centre for s in cfg.terminal_seeds if s.terminal_id == terminal_id)


def with_version_one(cfg: PortConfig, shapes: dict[str, Polygon]) -> list[Terminal]:
    """The real version-0 seeds plus a complete version-1 set.

    Complete, so that the only problems reported are the intended ones: a
    missing terminal at a version is itself a problem.
    """
    terminals: list[Terminal] = []
    for seed in cfg.terminal_seeds:
        for version, geom in (
            (0, seed_circle(seed.centre[0], seed.centre[1], cfg.seed_radius_m)),
            (1, shapes[seed.terminal_id]),
        ):
            terminals.append(
                Terminal(
                    terminal_id=seed.terminal_id,
                    name=seed.name,
                    crude=seed.crude,
                    version=version,
                    geom=geom,
                )
            )
    return terminals


def test_the_real_configuration_loads() -> None:
    cfg = load_port(PORT_ID)

    assert cfg.id == PORT_ID
    assert cfg.portwatch_portid == "port264"
    assert cfg.seed_radius_m == 700
    assert sorted(s.terminal_id for s in cfg.terminal_seeds) == SEED_IDS
    # YAML 1.1 would turn a bare `yes` into a boolean; the file quotes it.
    assert {s.crude for s in cfg.terminal_seeds} == {"yes", "uncertain"}


def test_the_gate_spans_the_jetties() -> None:
    cfg = load_port(PORT_ID)
    assert round(metres_between(cfg.seaward_gate.a, cfg.seaward_gate.b)) == 2972


def test_the_harbour_reference_is_on_the_harbour_side() -> None:
    cfg = load_port(PORT_ID)
    assert gate_offset_m(cfg, cfg.harbour_reference) < 0
    # A point out in the Gulf, well beyond the jetties.
    assert gate_offset_m(cfg, (27.75, -96.85)) > 0


def test_the_real_geojson_holds_three_sound_version_zero_terminals() -> None:
    cfg = load_port(PORT_ID)
    terminals = read_terminals_geojson(terminals_path(cfg))

    at_zero = [t for t in terminals if t.version == 0]
    assert len(at_zero) == 3
    assert sorted(t.terminal_id for t in at_zero) == SEED_IDS
    assert check_port(cfg, terminals) == []


def test_overlapping_version_one_polygons_report() -> None:
    cfg = load_port(PORT_ID)
    shapes = {
        # 900 m squares 1,486 m apart necessarily share water.
        "enbridge_ingleside": square(*centre_of(cfg, "enbridge_ingleside"), 900),
        "south_texas_gateway": square(*centre_of(cfg, "south_texas_gateway"), 900),
        "flint_hills_ingleside": square(*centre_of(cfg, "flint_hills_ingleside"), 200),
    }
    problems = check_port(cfg, with_version_one(cfg, shapes))

    assert any("overlap at version 1" in problem for problem in problems)


def test_a_version_one_polygon_on_another_seed_reports() -> None:
    cfg = load_port(PORT_ID)
    enbridge = centre_of(cfg, "enbridge_ingleside")
    flint_hills = centre_of(cfg, "flint_hills_ingleside")
    shapes = {
        # Stretched west from its own centre until it swallows the Flint Hills one.
        "enbridge_ingleside": box(
            min(enbridge[1], flint_hills[1]) - 0.001,
            min(enbridge[0], flint_hills[0]) - 0.001,
            max(enbridge[1], flint_hills[1]) + 0.001,
            max(enbridge[0], flint_hills[0]) + 0.001,
        ),
        "south_texas_gateway": square(*centre_of(cfg, "south_texas_gateway"), 150),
        "flint_hills_ingleside": square(*flint_hills, 150),
    }
    problems = check_port(cfg, with_version_one(cfg, shapes))

    assert (
        "enbridge_ingleside at version 1 reaches into the flint_hills_ingleside seed circle"
        in problems
    )


def test_a_version_one_polygon_inside_the_shared_seed_overlap_does_not_report() -> None:
    cfg = load_port(PORT_ID)
    # The South Texas Gateway and Flint Hills seed circles genuinely overlap by
    # about 416 m. This South Texas Gateway polygon reaches west into that shared
    # band and no further, so it trespasses on nothing exclusively Flint Hills'.
    shapes = {
        "south_texas_gateway": box(-97.2165, 27.8295, -97.2140, 27.8305),
        "flint_hills_ingleside": square(*centre_of(cfg, "flint_hills_ingleside"), 150),
        "enbridge_ingleside": square(*centre_of(cfg, "enbridge_ingleside"), 150),
    }
    assert check_port(cfg, with_version_one(cfg, shapes)) == []
