"""The state machine on synthetic tracks, one per case the plan enumerates."""

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from shapely import Polygon, box

from oiltracker.events.detect import (
    arrival_plateau,
    departure_candidates,
    detect_departure,
    detect_visits,
    hours_between,
)
from oiltracker.events.geometry import HARBOUR, ON_LINE, SEAWARD, gate_side
from oiltracker.events.model import Obs, Visit
from oiltracker.ports.config import M_PER_DEGREE_LAT, PortConfig, Terminal, load_port

T0 = datetime(2024, 3, 5, tzinfo=UTC)
STEP = timedelta(minutes=5)
SQUARE_M = 400.0
KNOT_MS = 0.514444

ENBRIDGE = (27.82, -97.20)
GATE_SEAWARD = (27.75, -96.85)


@pytest.fixture
def cfg() -> PortConfig:
    return load_port("corpus_christi")


def square(lat: float, lon: float, side_m: float) -> Polygon:
    half_lat = (side_m / 2) / M_PER_DEGREE_LAT
    half_lon = (side_m / 2) / (M_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return box(lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat)


@pytest.fixture
def terminals(cfg: PortConfig) -> list[Terminal]:
    """Version-0 squares 400 m across on the three configured centres."""
    return [
        Terminal(
            terminal_id=seed.terminal_id,
            name=seed.name,
            crude=seed.crude,
            version=0,
            geom=square(seed.centre[0], seed.centre[1], SQUARE_M),
        )
        for seed in cfg.terminal_seeds
    ]


def centre(cfg: PortConfig, terminal_id: str) -> tuple[float, float]:
    return next(s.centre for s in cfg.terminal_seeds if s.terminal_id == terminal_id)


def still(
    at: tuple[float, float], start: datetime, hours: float, sog: float, draught: float | None = None
) -> list[Obs]:
    """Observations every five minutes at one spot."""
    count = int(hours * 12)
    return [
        Obs(ts=start + STEP * i, lat=at[0], lon=at[1], sog=sog, draught_m=draught)
        for i in range(count)
    ]


def leg(
    start_at: tuple[float, float],
    end_at: tuple[float, float],
    start: datetime,
    knots: float,
    draught: float | None = None,
    include_first: bool = True,
) -> list[Obs]:
    """A straight run between two points at a steady speed, sampled every five minutes."""
    metres = _metres_between(start_at, end_at)
    steps = max(1, round(metres / (knots * KNOT_MS * STEP.total_seconds())))
    points: list[Obs] = []
    for i in range(0 if include_first else 1, steps + 1):
        fraction = i / steps
        points.append(
            Obs(
                ts=start + STEP * i,
                lat=start_at[0] + (end_at[0] - start_at[0]) * fraction,
                lon=start_at[1] + (end_at[1] - start_at[1]) * fraction,
                sog=knots,
                draught_m=draught,
            )
        )
    return points


def _metres_between(first: tuple[float, float], second: tuple[float, float]) -> float:
    mean_lat = math.radians((first[0] + second[0]) / 2)
    d_y = (second[0] - first[0]) * M_PER_DEGREE_LAT
    d_x = (second[1] - first[1]) * M_PER_DEGREE_LAT * math.cos(mean_lat)
    return math.hypot(d_x, d_y)


def shift(observations: Sequence[Obs], by: timedelta) -> list[Obs]:
    return [
        Obs(ts=one.ts + by, lat=one.lat, lon=one.lon, sog=one.sog, draught_m=one.draught_m)
        for one in observations
    ]


# --- the gate itself -------------------------------------------------------


def test_gate_side_names_both_sides_and_the_line(cfg: PortConfig) -> None:
    assert gate_side(*cfg.harbour_reference, cfg) == HARBOUR
    assert gate_side(*GATE_SEAWARD, cfg) == SEAWARD
    # The gate line is lat + lon = -69.21; its midpoint lies exactly on it.
    midpoint = (
        (cfg.seaward_gate.a[0] + cfg.seaward_gate.b[0]) / 2,
        (cfg.seaward_gate.a[1] + cfg.seaward_gate.b[1]) / 2,
    )
    assert gate_side(*midpoint, cfg) == ON_LINE


def test_a_crossing_beyond_the_segments_ends_is_not_a_crossing(cfg: PortConfig) -> None:
    """Far north of the jetties the same line separates the sides, but the gate does not."""
    north_harbour = Obs(ts=T0, lat=28.00, lon=-97.24, sog=8.0)
    north_seaward = Obs(ts=T0 + timedelta(minutes=30), lat=28.02, lon=-97.20, sog=8.0)
    visit = _visit_ending(T0)

    assert detect_departure([north_harbour, north_seaward], visit, None, cfg) is None


def _visit_ending(at: datetime) -> Visit:
    return Visit(
        terminal_id="enbridge_ingleside",
        polygon_version=0,
        entered_at=at - timedelta(hours=8),
        exited_at=at,
        dwell_hours=8.0,
        low_speed_fraction=1.0,
        observations=96,
        open_ended=False,
    )


def run_toward(
    start_at: tuple[float, float],
    target: tuple[float, float],
    start: datetime,
    hours: float,
    knots: float,
    draught: float | None = None,
    skip_first: bool = False,
) -> list[Obs]:
    """Travel from a point towards another, at a steady speed, for a fixed time.

    The target only sets the bearing: the track runs straight past it if the
    time allows.
    """
    span_m = _metres_between(start_at, target)
    step_m = knots * KNOT_MS * STEP.total_seconds()
    steps = int(hours * 3600 / STEP.total_seconds())

    points: list[Obs] = []
    for i in range(1 if skip_first else 0, steps + 1):
        fraction = (i * step_m) / span_m
        points.append(
            Obs(
                ts=start + STEP * i,
                lat=start_at[0] + (target[0] - start_at[0]) * fraction,
                lon=start_at[1] + (target[1] - start_at[1]) * fraction,
                sog=knots,
                draught_m=draught,
            )
        )
    return points


def gate_midpoint(cfg: PortConfig) -> tuple[float, float]:
    return (
        (cfg.seaward_gate.a[0] + cfg.seaward_gate.b[0]) / 2,
        (cfg.seaward_gate.a[1] + cfg.seaward_gate.b[1]) / 2,
    )


def berth_to_gate_hours(cfg: PortConfig, berth: tuple[float, float], knots: float) -> float:
    """What the transit actually takes, for the port's real geometry."""
    return _metres_between(berth, gate_midpoint(cfg)) / (knots * KNOT_MS) / 3600.0


def only_visit(
    observations: Sequence[Obs], terminals: Sequence[Terminal], cfg: PortConfig
) -> Visit:
    visits = detect_visits(observations, terminals, cfg)
    assert len(visits) == 1, f"expected one visit, got {visits}"
    return visits[0]


# --- (a) clean load and sail ----------------------------------------------


def point_along(
    start_at: tuple[float, float], target: tuple[float, float], metres: float
) -> tuple[float, float]:
    """A point `metres` from `start_at` on the bearing towards `target`."""
    fraction = metres / _metres_between(start_at, target)
    return (
        start_at[0] + (target[0] - start_at[0]) * fraction,
        start_at[1] + (target[1] - start_at[1]) * fraction,
    )


def track_a(cfg: PortConfig) -> list[Obs]:
    """Inbound in ballast, eight hours alongside, then out to sea loaded."""
    berth = centre(cfg, "enbridge_ingleside")
    offshore = gate_midpoint(cfg)

    # Two hours out at 8 kn, then run in and stop just short of T0, so the first
    # observation inside the polygon is the first one actually alongside.
    approach_from = point_along(berth, offshore, 2.0 * 8.0 * KNOT_MS * 3600)
    inbound = run_toward(approach_from, berth, T0 - timedelta(hours=2), 2.0, 8.0, draught=9.0)
    inbound = [one for one in inbound if one.ts < T0]

    alongside = still(berth, T0, 8.0 + 1 / 12, 0.1, draught=9.0)
    outbound = run_toward(
        berth, offshore, T0 + timedelta(hours=8), 5.0, 8.0, draught=15.5, skip_first=True
    )
    return [*inbound, *alongside, *outbound]


def test_a_clean_load_and_sail(cfg: PortConfig, terminals: list[Terminal]) -> None:
    observations = track_a(cfg)
    visit = only_visit(observations, terminals, cfg)

    assert visit.terminal_id == "enbridge_ingleside"
    assert visit.entered_at == T0
    assert visit.exited_at == T0 + timedelta(hours=8)
    assert visit.dwell_hours == pytest.approx(8.0)
    assert visit.low_speed_fraction == pytest.approx(1.0)
    assert visit.open_ended is False

    departure = detect_departure(observations, visit, None, cfg)
    assert departure is not None
    assert departure.gate_interpolated is False

    # The plan expected 3.0 h here, but at 8 kn the Enbridge berth is 1.07 h from
    # the gate, not three: 3 h would need 44 km of water where there is 15.8.
    expected = berth_to_gate_hours(cfg, centre(cfg, "enbridge_ingleside"), 8.0)
    assert hours_between(visit.exited_at, departure.gate_crossed_at) == pytest.approx(
        expected, abs=0.1
    )

    arrival = arrival_plateau(observations, visit, cfg)
    assert arrival is not None
    assert arrival.draught_m == pytest.approx(9.0)
    assert arrival.at == T0

    candidates = departure_candidates(observations, visit, cfg)
    assert [round(c.draught_m, 1) for c in candidates] == [9.0, 15.5]
    assert hours_between(visit.exited_at, candidates[0].at) == pytest.approx(0.0, abs=0.01)
    assert hours_between(visit.exited_at, candidates[1].at) == pytest.approx(5.0, abs=0.01)


# --- (b) berth then inland -------------------------------------------------


def test_b_berth_then_inland_is_a_visit_without_an_event(
    cfg: PortConfig, terminals: list[Terminal]
) -> None:
    berth = centre(cfg, "enbridge_ingleside")
    alongside = still(berth, T0, 8.0 + 1 / 12, 0.1)
    inland = run_toward(
        berth, cfg.harbour_reference, T0 + timedelta(hours=8), 2.0, 6.0, skip_first=True
    )
    observations = [*alongside, *inland]

    visit = only_visit(observations, terminals, cfg)
    assert visit.open_ended is False
    assert detect_departure(observations, visit, None, cfg) is None


# --- (c) offshore anchorage ------------------------------------------------


def test_c_offshore_anchorage_is_no_visit(cfg: PortConfig, terminals: list[Terminal]) -> None:
    observations = still(GATE_SEAWARD, T0, 20.0, 0.0)
    assert detect_visits(observations, terminals, cfg) == []


# --- (d) gap across exit ---------------------------------------------------


def test_d_a_gap_across_the_exit_interpolates_the_crossing(
    cfg: PortConfig, terminals: list[Terminal]
) -> None:
    berth = centre(cfg, "enbridge_ingleside")
    alongside = still(berth, T0, 8.0 + 1 / 12, 0.1)
    far_out = [Obs(ts=T0 + timedelta(hours=13), lat=27.78, lon=-96.95, sog=8.0)]
    observations = [*alongside, *far_out]

    visit = only_visit(observations, terminals, cfg)
    assert visit.exited_at == T0 + timedelta(hours=8)
    assert visit.open_ended is False

    departure = detect_departure(observations, visit, None, cfg)
    assert departure is not None
    assert departure.gate_interpolated is True
    assert hours_between(visit.exited_at, departure.gate_crossed_at) == pytest.approx(2.5)

    assert arrival_plateau(observations, visit, cfg) is None
    assert departure_candidates(observations, visit, cfg) == []


# --- (e) loiter ------------------------------------------------------------


def test_e_loitering_clear_of_every_polygon_is_no_visit(
    cfg: PortConfig, terminals: list[Terminal]
) -> None:
    observations = still((27.84, -97.18), T0, 10.0, 0.2)
    assert detect_visits(observations, terminals, cfg) == []


# --- (f) re-berth ----------------------------------------------------------


def test_f_two_berthings_give_two_events(cfg: PortConfig, terminals: list[Terminal]) -> None:
    first_berth = centre(cfg, "enbridge_ingleside")
    second_berth = centre(cfg, "south_texas_gateway")
    offshore = gate_midpoint(cfg)

    first_stay = still(first_berth, T0, 8.0 + 1 / 12, 0.1)
    exit1 = T0 + timedelta(hours=8)
    outbound = run_toward(first_berth, offshore, exit1, 3.0, 8.0, skip_first=True)
    waiting = still((27.72, -96.80), exit1 + timedelta(hours=3), 6.0, 0.0)

    second_entry = exit1 + timedelta(hours=20)
    inbound = run_toward((27.72, -96.80), second_berth, second_entry - timedelta(hours=3), 3.0, 8.0)
    inbound = [one for one in inbound if one.ts < second_entry]
    second_stay = still(second_berth, second_entry, 8.0 + 1 / 12, 0.1)
    exit2 = second_entry + timedelta(hours=8)
    final = run_toward(second_berth, offshore, exit2, 3.0, 8.0, skip_first=True)

    observations = [*first_stay, *outbound, *waiting, *inbound, *second_stay, *final]
    visits = detect_visits(observations, terminals, cfg)

    assert [v.terminal_id for v in visits] == ["enbridge_ingleside", "south_texas_gateway"]

    first_departure = detect_departure(observations, visits[0], visits[1].entered_at, cfg)
    second_departure = detect_departure(observations, visits[1], None, cfg)
    assert first_departure is not None
    assert second_departure is not None

    for visit, departure, berth in (
        (visits[0], first_departure, first_berth),
        (visits[1], second_departure, second_berth),
    ):
        expected = berth_to_gate_hours(cfg, berth, 8.0)
        assert hours_between(visit.exited_at, departure.gate_crossed_at) == pytest.approx(
            expected, abs=0.15
        )


# --- (g) jitter ------------------------------------------------------------


def test_g_a_brief_wander_outside_does_not_split_the_visit(
    cfg: PortConfig, terminals: list[Terminal]
) -> None:
    berth = centre(cfg, "enbridge_ingleside")
    alongside = still(berth, T0, 8.0 + 1 / 12, 0.1)

    # Three consecutive fixes 50 m clear of the 400 m square, then back inside.
    nudge = (SQUARE_M / 2 + 50) / M_PER_DEGREE_LAT
    jittered: list[Obs] = []
    for one in alongside:
        offset = timedelta(hours=4) <= (one.ts - T0) < timedelta(hours=4, minutes=15)
        jittered.append(
            Obs(
                ts=one.ts,
                lat=one.lat + (nudge if offset else 0.0),
                lon=one.lon,
                sog=one.sog,
                draught_m=one.draught_m,
            )
        )
    outbound = run_toward(
        berth, gate_midpoint(cfg), T0 + timedelta(hours=8), 3.0, 8.0, skip_first=True
    )
    observations = [*jittered, *outbound]

    visit = only_visit(observations, terminals, cfg)
    assert visit.entered_at == T0
    assert visit.exited_at == T0 + timedelta(hours=8)
    assert visit.observations == len(alongside) - 3

    assert detect_departure(observations, visit, None, cfg) is not None


# --- (h) open-ended --------------------------------------------------------


def test_h_a_track_that_stops_alongside_is_open_ended_and_has_no_event(
    cfg: PortConfig, terminals: list[Terminal]
) -> None:
    berth = centre(cfg, "enbridge_ingleside")
    observations = still(berth, T0, 6.0, 0.1)

    visit = only_visit(observations, terminals, cfg)
    assert visit.open_ended is True
    assert detect_departure(observations, visit, None, cfg) is None
