"""Consistency checks over a port's configuration and its polygons.

`check_port` reports; it never repairs. Every problem is one line naming what is
wrong and where, so `oil port check` and the Task 9 derivation can both stop on
the same evidence.
"""

import math
from collections.abc import Sequence
from itertools import combinations

from shapely import Point, Polygon, box

from oiltracker.ports.config import M_PER_DEGREE_LAT, PortConfig, Terminal, seed_circles

GATE_MIN_M = 500.0
GATE_MAX_M = 5_000.0
ON_THE_LINE_M = 1.0
"""How near the gate line counts as on it."""


def metres_between(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Flat-earth distance in metres between two (lat, lon) points."""
    mean_lat = math.radians((first[0] + second[0]) / 2)
    d_y = (second[0] - first[0]) * M_PER_DEGREE_LAT
    d_x = (second[1] - first[1]) * M_PER_DEGREE_LAT * math.cos(mean_lat)
    return math.hypot(d_x, d_y)


def gate_offset_m(cfg: PortConfig, point: tuple[float, float]) -> float:
    """Signed distance in metres from the gate line, positive on the seaward side.

    The sign is the cross product of the gate vector with the vector to the
    point, oriented so that `harbour_reference` is negative.
    """
    a, b = cfg.seaward_gate.a, cfg.seaward_gate.b
    mean_lat = math.radians((a[0] + b[0]) / 2)
    scale = M_PER_DEGREE_LAT * math.cos(mean_lat)

    gate_x = (b[1] - a[1]) * scale
    gate_y = (b[0] - a[0]) * M_PER_DEGREE_LAT
    to_x = (point[1] - a[1]) * scale
    to_y = (point[0] - a[0]) * M_PER_DEGREE_LAT

    cross = gate_x * to_y - gate_y * to_x
    length = math.hypot(gate_x, gate_y)
    offset = cross / length if length else 0.0

    reference = cfg.harbour_reference
    ref_cross = gate_x * ((reference[0] - a[0]) * M_PER_DEGREE_LAT) - gate_y * (
        (reference[1] - a[1]) * scale
    )
    return -offset if ref_cross > 0 else offset


def check_port(cfg: PortConfig, terminals: Sequence[Terminal]) -> list[str]:
    """Every problem in the port's geometry, one string each. Empty means sound."""
    problems: list[str] = []
    seeds = seed_circles(cfg)
    bounds = box(cfg.bbox.lon_min, cfg.bbox.lat_min, cfg.bbox.lon_max, cfg.bbox.lat_max)

    gate_m = metres_between(cfg.seaward_gate.a, cfg.seaward_gate.b)
    if gate_m < GATE_MIN_M or gate_m > GATE_MAX_M:
        problems.append(f"gate is {gate_m:.0f} m, outside {GATE_MIN_M:.0f}-{GATE_MAX_M:.0f} m")

    reference = Point(cfg.harbour_reference[1], cfg.harbour_reference[0])
    if not bounds.covers(reference):
        problems.append("harbour_reference is outside the bounding box")
    if abs(gate_offset_m(cfg, cfg.harbour_reference)) < ON_THE_LINE_M:
        problems.append("harbour_reference sits on the gate line")
    for terminal_id, circle in seeds.items():
        if circle.covers(reference):
            problems.append(f"harbour_reference is inside the {terminal_id} seed circle")

    for terminal in terminals:
        where = f"{terminal.terminal_id} at version {terminal.version}"
        if not terminal.geom.is_valid:
            problems.append(f"{where} is not a valid polygon")
        if not bounds.covers(terminal.geom):
            problems.append(f"{where} is not inside the bounding box")

    problems.extend(_check_version_zero(cfg, terminals, seeds))
    for version in sorted({t.version for t in terminals if t.version >= 1}):
        problems.extend(_check_derived_version(cfg, terminals, seeds, version))
    return problems


def _check_version_zero(
    cfg: PortConfig, terminals: Sequence[Terminal], seeds: dict[str, Polygon]
) -> list[str]:
    """Version 0 is exactly one seed circle per configured terminal."""
    problems: list[str] = []
    at_zero = [t for t in terminals if t.version == 0]
    for seed in cfg.terminal_seeds:
        matching = [t for t in at_zero if t.terminal_id == seed.terminal_id]
        if len(matching) != 1:
            problems.append(
                f"{seed.terminal_id} has {len(matching)} polygons at version 0, expected 1"
            )
            continue
        if not matching[0].geom.equals_exact(seeds[seed.terminal_id], tolerance=1e-9):
            problems.append(f"{seed.terminal_id} at version 0 is not its seed circle")

    configured = {seed.terminal_id for seed in cfg.terminal_seeds}
    for extra in sorted({t.terminal_id for t in at_zero} - configured):
        problems.append(f"{extra} at version 0 is not a configured terminal")
    return problems


def _check_derived_version(
    cfg: PortConfig, terminals: Sequence[Terminal], seeds: dict[str, Polygon], version: int
) -> list[str]:
    """A derived version covers each terminal once, without straying onto its neighbours."""
    problems: list[str] = []
    at_version = [t for t in terminals if t.version == version]

    for seed in cfg.terminal_seeds:
        matching = [t for t in at_version if t.terminal_id == seed.terminal_id]
        if len(matching) != 1:
            problems.append(
                f"{seed.terminal_id} has {len(matching)} polygons at version {version}, expected 1"
            )

    for first, second in combinations(at_version, 2):
        if first.geom.intersection(second.geom).area > 0:
            problems.append(
                f"{first.terminal_id} and {second.terminal_id} overlap at version {version}"
            )

    for terminal in at_version:
        own = seeds.get(terminal.terminal_id)
        if own is None:
            problems.append(
                f"{terminal.terminal_id} at version {version} is not a configured terminal"
            )
            continue
        if not terminal.geom.intersects(own):
            problems.append(
                f"{terminal.terminal_id} at version {version} misses its own seed circle"
            )
        for other_id, other in seeds.items():
            if other_id == terminal.terminal_id:
                continue
            # Only the part of the neighbour's seed that is not also ours: the
            # STG and Flint Hills seeds genuinely overlap, and reaching into
            # that shared water is not a trespass.
            exclusive = other.difference(own)
            if terminal.geom.intersection(exclusive).area > 0:
                problems.append(
                    f"{terminal.terminal_id} at version {version} reaches into the "
                    f"{other_id} seed circle"
                )
    return problems
