"""The canonical AIS columns, and how each archive's header maps onto them.

The archives disagree about spelling and column order: the zip releases use
CamelCase with `LAT` before `LON`, the zst releases snake_case with longitude
first. Mapping is by name, so the order never matters.
"""

import pyarrow as pa

PARQUET_SCHEMA: pa.Schema = pa.schema(
    [
        ("mmsi", pa.int32()),
        ("ts", pa.timestamp("us", tz="UTC")),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("sog", pa.float32()),
        ("cog", pa.float32()),
        ("heading", pa.float32()),
        ("vessel_name", pa.string()),
        ("imo", pa.int32()),
        ("call_sign", pa.string()),
        ("vessel_type", pa.int16()),
        ("status", pa.int16()),
        ("length_m", pa.float32()),
        ("width_m", pa.float32()),
        ("draught_m", pa.float32()),
        ("cargo", pa.int16()),
        ("transceiver_class", pa.string()),
    ]
)

CANONICAL_COLUMNS: list[str] = list(PARQUET_SCHEMA.names)

# Source column name, lowercased, to canonical name. Both spellings of every
# column the probe found, so one mapping serves every year.
CANONICAL_BY_SOURCE: dict[str, str] = {
    "mmsi": "mmsi",
    "basedatetime": "ts",
    "base_date_time": "ts",
    "lat": "lat",
    "latitude": "lat",
    "lon": "lon",
    "longitude": "lon",
    "sog": "sog",
    "cog": "cog",
    "heading": "heading",
    "vesselname": "vessel_name",
    "vessel_name": "vessel_name",
    "imo": "imo",
    "callsign": "call_sign",
    "call_sign": "call_sign",
    "vesseltype": "vessel_type",
    "vessel_type": "vessel_type",
    "status": "status",
    "length": "length_m",
    "width": "width_m",
    "draft": "draught_m",
    "draught": "draught_m",
    "cargo": "cargo",
    "transceiverclass": "transceiver_class",
    "transceiver": "transceiver_class",
}

# AIS codes 80 to 89 are tankers, in VesselType and in Cargo alike.
TANKER_CODE_MIN = 80
TANKER_CODE_MAX = 89

# Without these a row is useless; a header missing one is a hard error.
REQUIRED_COLUMNS: frozenset[str] = frozenset({"mmsi", "ts", "lat", "lon"})

# Out-of-band values each field uses for "not reported". Every one becomes null.
SOG_NOT_REPORTED = 102.3
COG_NOT_REPORTED = 360.0
HEADING_NOT_REPORTED = 511.0

# Read from CSV as text and converted afterwards: some releases write IMO9999999.
IMO_SOURCE_TYPE: pa.DataType = pa.string()

CSV_TYPES: dict[str, pa.DataType] = {
    "mmsi": pa.int32(),
    "ts": pa.timestamp("us"),
    "lat": pa.float64(),
    "lon": pa.float64(),
    "sog": pa.float32(),
    "cog": pa.float32(),
    "heading": pa.float32(),
    "vessel_name": pa.string(),
    "imo": IMO_SOURCE_TYPE,
    "call_sign": pa.string(),
    "vessel_type": pa.int16(),
    "status": pa.int16(),
    "length_m": pa.float32(),
    "width_m": pa.float32(),
    "draught_m": pa.float32(),
    "cargo": pa.int16(),
    "transceiver_class": pa.string(),
}


def canonical_for(header: list[str], year: int) -> dict[str, str]:
    """Map this archive's column names to canonical ones, or say what is missing."""
    mapping = {
        name: CANONICAL_BY_SOURCE[name.strip().lower()]
        for name in header
        if name.strip().lower() in CANONICAL_BY_SOURCE
    }
    missing = REQUIRED_COLUMNS - set(mapping.values())
    if missing:
        raise ValueError(
            f"the {year} archive header is missing {sorted(missing)}; header was {header}"
        )
    return mapping
