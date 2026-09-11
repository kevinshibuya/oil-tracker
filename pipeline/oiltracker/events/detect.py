"""The berth-visit state machine, as a set of pure functions over one vessel's track.

The rules are the plan's, numbered here as they are numbered there. Nothing in
this module touches a database, so every rule is testable on a synthetic track.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from oiltracker.events.geometry import (
    HARBOUR,
    ON_LINE,
    SEAWARD,
    gate_crossing,
    gate_side,
    terminal_at,
)
from oiltracker.events.model import Departure, Obs, Plateau, Visit
from oiltracker.ports.config import PortConfig, Terminal


@dataclass(frozen=True)
class _Labelled:
    """Rule 1: an observation with the terminal covering it and its side of the gate."""

    obs: Obs
    terminal: Terminal | None
    side: int


def _label(obs: Sequence[Obs], terminals: Sequence[Terminal], cfg: PortConfig) -> list[_Labelled]:
    return [
        _Labelled(
            obs=one,
            terminal=terminal_at(one.lat, one.lon, terminals),
            side=gate_side(one.lat, one.lon, cfg),
        )
        for one in obs
    ]


def hours_between(earlier: datetime, later: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


def detect_visits(
    obs: Sequence[Obs], terminals: Sequence[Terminal], cfg: PortConfig
) -> list[Visit]:
    """Rules 2 and 3: the vessel's runs alongside a terminal, filtered to berthings."""
    labelled = _label(obs, terminals, cfg)
    visits: list[Visit] = []

    index = 0
    while index < len(labelled):
        if labelled[index].terminal is None:
            index += 1
            continue

        run, resume = _run_from(labelled, index, cfg)
        visit = _as_visit(run, cfg)
        if visit is not None:
            visits.append(visit)
        index = resume
    return visits


@dataclass
class _Run:
    """Rule 2: a stay at one terminal, plus where processing resumes after it."""

    terminal: Terminal
    entered_at: datetime
    exited_at: datetime
    inside: list[Obs]
    open_ended: bool


def _run_from(labelled: Sequence[_Labelled], start: int, cfg: PortConfig) -> tuple[_Run, int]:
    """Walk one run from an observation known to be inside a terminal.

    Returns the run and the index of the observation to resume at: the one after
    the run's exit, so an excursion that ended the run can open another.
    """
    terminal = labelled[start].terminal
    assert terminal is not None

    inside = [labelled[start].obs]
    last_inside_index = start
    excursion_started_at: datetime | None = None

    index = start + 1
    while index < len(labelled):
        current = labelled[index]
        here = current.terminal is not None and current.terminal.terminal_id == terminal.terminal_id

        if here:
            # A gap, however long, does not split a stay that resumes alongside.
            inside.append(current.obs)
            last_inside_index = index
            excursion_started_at = None
            index += 1
            continue

        last_inside_at = labelled[last_inside_index].obs.ts
        gap_hours = hours_between(last_inside_at, current.obs.ts)
        if excursion_started_at is None:
            excursion_started_at = current.obs.ts
        away_minutes = (current.obs.ts - excursion_started_at).total_seconds() / 60.0

        ended = gap_hours > cfg.gap_tolerance_hours or away_minutes >= cfg.exit_confirm_minutes
        if ended:
            return (
                _Run(terminal, inside[0].ts, last_inside_at, inside, open_ended=False),
                last_inside_index + 1,
            )
        index += 1

    return (
        _Run(terminal, inside[0].ts, inside[-1].ts, inside, open_ended=True),
        len(labelled),
    )


def _as_visit(run: _Run, cfg: PortConfig) -> Visit | None:
    """Rule 3: a run becomes a visit only if it is long enough and slow enough."""
    dwell_hours = hours_between(run.entered_at, run.exited_at)
    speeds = [one.sog for one in run.inside if one.sog is not None]
    low_speed_fraction = (
        sum(1 for speed in speeds if speed < cfg.low_speed_knots) / len(speeds) if speeds else 0.0
    )

    if dwell_hours < cfg.dwell_min_hours or low_speed_fraction < cfg.low_speed_fraction_min:
        return None

    return Visit(
        terminal_id=run.terminal.terminal_id,
        polygon_version=run.terminal.version,
        entered_at=run.entered_at,
        exited_at=run.exited_at,
        dwell_hours=dwell_hours,
        low_speed_fraction=low_speed_fraction,
        observations=len(run.inside),
        open_ended=run.open_ended,
    )


def detect_departure(
    obs: Sequence[Obs], visit: Visit, next_entered_at: datetime | None, cfg: PortConfig
) -> Departure | None:
    """Rule 4: the first seaward gate crossing after a visit, if there is one.

    An open-ended visit gets no scan: the track simply stops, and a vessel still
    alongside has not departed.
    """
    if visit.open_ended:
        return None

    horizon = visit.exited_at + timedelta(hours=cfg.departure_window_hours)
    if next_entered_at is not None:
        horizon = min(horizon, next_entered_at)

    scanned = [
        (one, gate_side(one.lat, one.lon, cfg))
        for one in obs
        if visit.exited_at <= one.ts <= horizon
    ]
    scanned = [pair for pair in scanned if pair[1] != ON_LINE]
    if len(scanned) < 2:
        return None

    previous, previous_side = scanned[0]
    for current, current_side in scanned[1:]:
        if previous_side == HARBOUR and current_side == SEAWARD:
            departure = _crossing_between(previous, current, cfg)
            if departure is not None:
                return departure
        previous, previous_side = current, current_side
    return None


def _crossing_between(previous: Obs, current: Obs, cfg: PortConfig) -> Departure | None:
    """Either the track really crosses the gate, or the gap is too big to tell."""
    fraction = gate_crossing(previous, current, cfg)
    if fraction is not None:
        span = current.ts - previous.ts
        return Departure(
            gate_crossed_at=previous.ts + span * fraction,
            gate_interpolated=False,
        )

    if hours_between(previous.ts, current.ts) > cfg.gap_tolerance_hours:
        return Departure(
            gate_crossed_at=previous.ts + (current.ts - previous.ts) / 2,
            gate_interpolated=True,
        )
    return None


def plateaus(obs: Sequence[Obs], start: datetime, end: datetime, cfg: PortConfig) -> list[Plateau]:
    """Rule 5: stretches of draught readings that stay within the tolerance band."""
    readings = [one for one in obs if one.draught_m is not None and start <= one.ts <= end]

    found: list[Plateau] = []
    run: list[Obs] = []

    def close() -> None:
        plateau = _as_plateau(run, cfg)
        if plateau is not None:
            found.append(plateau)

    for reading in readings:
        candidate = [*run, reading]
        draughts = [one.draught_m for one in candidate if one.draught_m is not None]
        if run and max(draughts) - min(draughts) > cfg.plateau_max_range_m:
            close()
            run = [reading]
        else:
            run = candidate
    close()
    return found


def _as_plateau(run: Sequence[Obs], cfg: PortConfig) -> Plateau | None:
    if len(run) < cfg.plateau_min_obs:
        return None
    span_minutes = (run[-1].ts - run[0].ts).total_seconds() / 60.0
    if span_minutes < cfg.plateau_min_span_minutes:
        return None

    draughts = [one.draught_m for one in run if one.draught_m is not None]
    return Plateau(
        draught_m=statistics.median(draughts),
        at=run[-1].ts,
        observations=len(run),
    )


def arrival_plateau(obs: Sequence[Obs], visit: Visit, cfg: PortConfig) -> Plateau | None:
    """Rule 6: the last steady draught before the vessel came alongside."""
    window_start = visit.entered_at - timedelta(hours=cfg.arrival_lookback_hours)
    found = plateaus(obs, window_start, visit.entered_at, cfg)
    return found[-1] if found else None


def departure_candidates(obs: Sequence[Obs], visit: Visit, cfg: PortConfig) -> list[Plateau]:
    """Rule 6: every steady draught around the moment the vessel left the berth.

    The window opens before the exit by design: the plateau that ends at berth
    exit is the loaded reading, and it is a candidate like any other.
    """
    window_start = visit.exited_at - timedelta(hours=cfg.candidates_before_exit_hours)
    window_end = visit.exited_at + timedelta(hours=cfg.candidates_after_exit_hours)
    return plateaus(obs, window_start, window_end, cfg)
