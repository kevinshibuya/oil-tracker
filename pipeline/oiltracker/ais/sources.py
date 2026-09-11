"""Which archive serves a given year, and the URL of a given day within it."""

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, TypeAdapter

SourceFormat = Literal["csv_zip", "csv_zst", "parquet"]


class SourceSpec(BaseModel):
    """One archive that serves whole days of national AIS for a year.

    `url_pattern` carries the placeholders `{Y}`, `{M}` and `{D}`; `header` is
    the column list read verbatim from that archive's first day.
    """

    url_pattern: str
    format: SourceFormat
    header: list[str]


# Probed in this order; the first that answers for a year becomes its primary.
CANDIDATES: tuple[tuple[str, SourceFormat], ...] = (
    ("https://noaaocm.blob.core.windows.net/ais/csv2/csv{Y}/ais-{Y}-{M}-{D}.csv.zst", "csv_zst"),
    ("https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{Y}/AIS_{Y}_{M}_{D}.zip", "csv_zip"),
    ("https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{Y}/AIS_{Y}_{M}_{D}.parquet", "parquet"),
)

SOURCES_ADAPTER: TypeAdapter[dict[int, list[SourceSpec]]] = TypeAdapter(dict[int, list[SourceSpec]])


def url_for(spec: SourceSpec, day: date) -> str:
    """The archive URL of one day. Month and day are always two digits."""
    return (
        spec.url_pattern.replace("{Y}", f"{day.year:04d}")
        .replace("{M}", f"{day.month:02d}")
        .replace("{D}", f"{day.day:02d}")
    )


def load_sources(path: Path) -> dict[int, list[SourceSpec]]:
    """Read the probe's output: per year, its working sources in probe order."""
    raw: object = yaml.safe_load(path.read_text())
    return SOURCES_ADAPTER.validate_python(raw)


def write_sources(path: Path, sources: Mapping[int, Sequence[SourceSpec]]) -> None:
    """Write the probe's output, years ascending, source order preserved."""
    payload = {
        year: [spec.model_dump() for spec in specs] for year, specs in sorted(sources.items())
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, default_flow_style=False))
