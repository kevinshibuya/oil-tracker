"""Where an observation is: which terminal, which side of the gate, and when it crossed.

Everything works in a flat metre frame anchored at the gate's first point. Over
the few kilometres that matter here the distortion is far below AIS precision,
and it keeps the crossing fraction a true distance ratio.
"""

import math
from collections.abc import Sequence

from shapely import Point

from oiltracker.events.model import Obs
from oiltracker.ports.config import M_PER_DEGREE_LAT, PortConfig, Terminal

ON_THE_LINE_M = 1.0
"""Nearer than this to the gate line counts as exactly on it."""

HARBOUR = -1
SEAWARD = 1
ON_LINE = 0


def terminal_at(lat: float, lon: float, terminals: Sequence[Terminal]) -> Terminal | None:
    """The terminal whose polygon covers the point, nearest centroid wins a tie.

    Version-0 seed circles can overlap, so a point really can sit in two.
    """
    point = Point(lon, lat)
    covering = [terminal for terminal in terminals if terminal.geom.covers(point)]
    if not covering:
        return None
    if len(covering) == 1:
        return covering[0]
    return min(covering, key=lambda terminal: terminal.geom.centroid.distance(point))


def metres_frame(cfg: PortConfig, lat: float, lon: float) -> tuple[float, float]:
    """(x east, y north) in metres, relative to the gate's first point."""
    anchor = cfg.seaward_gate.a
    mean_lat = math.radians((cfg.seaward_gate.a[0] + cfg.seaward_gate.b[0]) / 2)
    x = (lon - anchor[1]) * M_PER_DEGREE_LAT * math.cos(mean_lat)
    y = (lat - anchor[0]) * M_PER_DEGREE_LAT
    return x, y


def gate_side(lat: float, lon: float, cfg: PortConfig) -> int:
    """-1 on the harbour side, +1 seaward, 0 on the line.

    The sign is the cross product of the gate vector with the vector to the
    point, flipped if need be so that `harbour_reference` comes out negative.
    """
    offset = _signed_offset_m(cfg, lat, lon)
    if abs(offset) < ON_THE_LINE_M:
        return ON_LINE
    return SEAWARD if offset > 0 else HARBOUR


def gate_crossing(p: Obs, q: Obs, cfg: PortConfig) -> float | None:
    """The fraction along p->q at which it meets the gate segment, or None.

    None when the two segments do not actually meet: a vessel can be harbour-side
    at one observation and seaward at the next without the straight line between
    them passing between the jetties.
    """
    px, py = metres_frame(cfg, p.lat, p.lon)
    qx, qy = metres_frame(cfg, q.lat, q.lon)
    bx, by = metres_frame(cfg, cfg.seaward_gate.b[0], cfg.seaward_gate.b[1])

    # Gate runs from the frame's origin (gate point a) to b.
    rx, ry = qx - px, qy - py
    denominator = rx * by - ry * bx
    if denominator == 0:
        return None

    along_track = ((0.0 - px) * by - (0.0 - py) * bx) / denominator
    along_gate = ((0.0 - px) * ry - (0.0 - py) * rx) / denominator
    if 0.0 <= along_track <= 1.0 and 0.0 <= along_gate <= 1.0:
        return along_track
    return None


def _signed_offset_m(cfg: PortConfig, lat: float, lon: float) -> float:
    bx, by = metres_frame(cfg, cfg.seaward_gate.b[0], cfg.seaward_gate.b[1])
    px, py = metres_frame(cfg, lat, lon)
    length = math.hypot(bx, by)
    if length == 0:
        return 0.0

    cross = (bx * py - by * px) / length
    reference = cfg.harbour_reference
    rx, ry = metres_frame(cfg, reference[0], reference[1])
    reference_cross = bx * ry - by * rx
    return -cross if reference_cross > 0 else cross
