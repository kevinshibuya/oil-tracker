"""The values the detection state machine passes around.

All pure data: the state machine is a set of functions over these, so every rule
in the plan can be tested on a synthetic track without a database.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Obs:
    """One AIS observation of one vessel."""

    ts: datetime
    lat: float
    lon: float
    sog: float | None = None
    draught_m: float | None = None


@dataclass(frozen=True)
class Visit:
    """A stay alongside one terminal, long enough and slow enough to be a berthing."""

    terminal_id: str
    polygon_version: int
    entered_at: datetime
    exited_at: datetime
    dwell_hours: float
    low_speed_fraction: float
    observations: int
    open_ended: bool


@dataclass(frozen=True)
class Plateau:
    """A stretch of draught readings steady enough to be one loaded condition."""

    draught_m: float
    at: datetime
    observations: int


@dataclass(frozen=True)
class Departure:
    """The moment a vessel crossed the seaward gate after a visit."""

    gate_crossed_at: datetime
    gate_interpolated: bool
