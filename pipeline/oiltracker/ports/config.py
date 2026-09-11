"""The port's configuration, its seed circles, and the geojson of record.

Every polygon version for a port lives in one geojson file: version 0 is the
seed circles, and each derivation appends a higher version. The file, not the
database, is the record.
"""

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, TypeAdapter
from shapely import Polygon

from oiltracker.paths import PORTS_DIR

Crude = Literal["yes", "uncertain"]
LatLon = tuple[float, float]

M_PER_DEGREE_LAT = 111_320.0
"""Metres per degree of latitude, and per degree of longitude at the equator."""

SEED_VERTICES = 64


class BBox(BaseModel):
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


class Gate(BaseModel):
    """The seaward gate: a line segment a vessel crosses on its way out."""

    a: LatLon
    b: LatLon


class TerminalSeed(BaseModel):
    """A terminal's published centre, before any polygon is derived for it."""

    terminal_id: str
    name: str
    crude: Crude
    centre: LatLon


class PortConfig(BaseModel):
    id: str
    name: str
    bbox: BBox
    seaward_gate: Gate
    harbour_reference: LatLon
    terminal_seeds: list[TerminalSeed]

    seed_radius_m: float = 700
    dwell_min_hours: float = 4
    low_speed_knots: float = 0.5
    low_speed_fraction_min: float = 0.8
    gap_tolerance_hours: float = 3
    exit_confirm_minutes: float = 30
    departure_window_hours: float = 36
    arrival_lookback_hours: float = 12
    candidates_before_exit_hours: float = 6
    candidates_after_exit_hours: float = 24
    plateau_min_obs: int = 3
    plateau_min_span_minutes: float = 30
    plateau_max_range_m: float = 0.2

    terminals_file: str
    portwatch_portid: str = "port264"


@dataclass(frozen=True)
class Terminal:
    """One terminal's polygon at one version."""

    terminal_id: str
    name: str
    crude: Crude
    version: int
    geom: Polygon


PORT_ADAPTER: TypeAdapter[PortConfig] = TypeAdapter(PortConfig)


def load_port(port_id: str, ports_dir: Path = PORTS_DIR) -> PortConfig:
    """Read `<port_id>.yaml` from the ports directory."""
    raw: object = yaml.safe_load((ports_dir / f"{port_id}.yaml").read_text())
    return PORT_ADAPTER.validate_python(raw)


def terminals_path(cfg: PortConfig, ports_dir: Path = PORTS_DIR) -> Path:
    """The geojson of record for this port's polygons."""
    return ports_dir / cfg.terminals_file


def seed_circle(lat: float, lon: float, radius_m: float) -> Polygon:
    """A 64-vertex circle of `radius_m` around a point, in WGS84 degrees.

    Flat-earth scaling: metres per degree of longitude shrink with the cosine of
    the latitude. Over a 700 m radius at 27.8 N the error is far below the
    precision of the published centres.
    """
    d_lat = radius_m / M_PER_DEGREE_LAT
    d_lon = radius_m / (M_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    ring = [
        (
            lon + d_lon * math.cos(2 * math.pi * i / SEED_VERTICES),
            lat + d_lat * math.sin(2 * math.pi * i / SEED_VERTICES),
        )
        for i in range(SEED_VERTICES)
    ]
    return Polygon(ring)


def seed_circles(cfg: PortConfig) -> dict[str, Polygon]:
    """Every configured terminal's seed circle, by terminal id."""
    return {
        seed.terminal_id: seed_circle(seed.centre[0], seed.centre[1], cfg.seed_radius_m)
        for seed in cfg.terminal_seeds
    }


def _ring(coords: Iterable[tuple[float, ...]]) -> list[tuple[float, float]]:
    return [(point[0], point[1]) for point in coords]


class PolygonGeometry(BaseModel):
    """A geojson Polygon: an exterior ring first, then any holes."""

    type: Literal["Polygon"]
    coordinates: list[list[tuple[float, float]]]

    def to_shapely(self) -> Polygon:
        return Polygon(shell=self.coordinates[0], holes=self.coordinates[1:])

    @classmethod
    def of(cls, polygon: Polygon) -> "PolygonGeometry":
        # Shapely coordinates may carry a z; geojson here is strictly 2D.
        rings = [_ring(polygon.exterior.coords)]
        rings.extend(_ring(interior.coords) for interior in polygon.interiors)
        return cls(type="Polygon", coordinates=rings)


class TerminalProperties(BaseModel):
    terminal_id: str
    name: str
    crude: Crude
    version: int


class TerminalFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    properties: TerminalProperties
    geometry: PolygonGeometry


class TerminalCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[TerminalFeature]


COLLECTION_ADAPTER: TypeAdapter[TerminalCollection] = TypeAdapter(TerminalCollection)


def read_terminals_geojson(path: Path) -> list[Terminal]:
    """Every polygon version in the file, in the order it is written."""
    raw: object = json.loads(path.read_text())
    collection = COLLECTION_ADAPTER.validate_python(raw)
    return [
        Terminal(
            terminal_id=feature.properties.terminal_id,
            name=feature.properties.name,
            crude=feature.properties.crude,
            version=feature.properties.version,
            geom=feature.geometry.to_shapely(),
        )
        for feature in collection.features
    ]


def write_terminals_geojson(path: Path, terminals: Sequence[Terminal]) -> None:
    """Write a feature collection, versions ascending then terminal id."""
    ordered = sorted(terminals, key=lambda t: (t.version, t.terminal_id))
    collection = TerminalCollection(
        features=[
            TerminalFeature(
                properties=TerminalProperties(
                    terminal_id=t.terminal_id,
                    name=t.name,
                    crude=t.crude,
                    version=t.version,
                ),
                geometry=PolygonGeometry.of(t.geom),
            )
            for t in ordered
        ]
    )
    path.write_text(collection.model_dump_json(indent=1) + "\n")
