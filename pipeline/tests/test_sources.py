"""URL construction and the YAML round-trip for probed AIS sources."""

from datetime import date
from pathlib import Path

from oiltracker.ais.sources import SourceSpec, load_sources, url_for, write_sources

HEADER_2023 = [
    "MMSI",
    "BaseDateTime",
    "LAT",
    "LON",
    "SOG",
    "COG",
    "Heading",
    "VesselName",
    "IMO",
    "CallSign",
    "VesselType",
    "Status",
    "Length",
    "Width",
    "Draft",
    "Cargo",
    "TransceiverClass",
]


def test_url_for_zero_pads_month_and_day() -> None:
    zst = SourceSpec(
        url_pattern="https://example.invalid/csv{Y}/ais-{Y}-{M}-{D}.csv.zst",
        format="csv_zst",
        header=HEADER_2023,
    )
    zip_spec = SourceSpec(
        url_pattern="https://example.invalid/{Y}/AIS_{Y}_{M}_{D}.zip",
        format="csv_zip",
        header=HEADER_2023,
    )

    assert (
        url_for(zst, date(2024, 3, 5)) == "https://example.invalid/csv2024/ais-2024-03-05.csv.zst"
    )
    assert url_for(zip_spec, date(2023, 1, 1)) == "https://example.invalid/2023/AIS_2023_01_01.zip"
    assert url_for(zst, date(2025, 12, 31)) == (
        "https://example.invalid/csv2025/ais-2025-12-31.csv.zst"
    )


def test_yaml_round_trips_a_two_source_year(tmp_path: Path) -> None:
    sources = {
        2023: [
            SourceSpec(
                url_pattern="https://example.invalid/csv{Y}/ais-{Y}-{M}-{D}.csv.zst",
                format="csv_zst",
                header=HEADER_2023,
            ),
            SourceSpec(
                url_pattern="https://example.invalid/{Y}/AIS_{Y}_{M}_{D}.zip",
                format="csv_zip",
                header=HEADER_2023,
            ),
        ]
    }

    path = tmp_path / "ais_sources.yaml"
    write_sources(path, sources)

    assert load_sources(path) == sources
