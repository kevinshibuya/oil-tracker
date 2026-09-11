"""The IMF PortWatch daily port-call series, which milestone 1 is measured against."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx
import psycopg
from psycopg.rows import TupleRow
from pydantic import BaseModel, TypeAdapter

ENDPOINT = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/"
    "Daily_Ports_Data/FeatureServer/0/query"
)

PAGE_SIZE = 2000
OUT_FIELDS = "date,portid,portcalls,portcalls_tanker,export_tanker,import_tanker"

UPSERT = """
insert into portwatch_daily (
    date, portid, portcalls, portcalls_tanker, export_tanker_t, import_tanker_t, fetched_at)
values (%s, %s, %s, %s, %s, %s, now())
on conflict (date, portid) do update set
    portcalls = excluded.portcalls,
    portcalls_tanker = excluded.portcalls_tanker,
    export_tanker_t = excluded.export_tanker_t,
    import_tanker_t = excluded.import_tanker_t,
    fetched_at = excluded.fetched_at
"""


@dataclass(frozen=True)
class PortwatchRow:
    """One port's traffic on one day."""

    date: date
    portid: str
    portcalls: int | None
    portcalls_tanker: int | None
    export_tanker_t: int | None
    import_tanker_t: int | None


class _Attributes(BaseModel):
    """The feature payload.

    `date` arrives either as epoch milliseconds or, as the live service sends it
    today, as an ISO date string: the field is declared esriFieldTypeDateOnly.
    Both are accepted. The tonnages are floats in tonnes.
    """

    date: int | str | None = None
    portid: str | None = None
    portname: str | None = None
    portcalls: float | None = None
    portcalls_tanker: float | None = None
    export_tanker: float | None = None
    import_tanker: float | None = None


class _Feature(BaseModel):
    attributes: _Attributes


class _Page(BaseModel):
    features: list[_Feature] = []
    exceededTransferLimit: bool = False  # the service's own spelling


PAGE_ADAPTER: TypeAdapter[_Page] = TypeAdapter(_Page)


def fetch_portwatch(client: httpx.Client, portid: str, from_date: date) -> list[PortwatchRow]:
    """Every daily record for one port from `from_date` on, following the paging."""
    rows: list[PortwatchRow] = []
    offset = 0
    while True:
        page = _query(
            client,
            {
                "where": f"portid='{portid}' AND date>=DATE '{from_date.isoformat()}'",
                "outFields": OUT_FIELDS,
                "orderByFields": "date ASC",
                "returnGeometry": "false",
                "f": "json",
                "resultRecordCount": str(PAGE_SIZE),
                "resultOffset": str(offset),
            },
        )
        rows.extend(_as_row(feature.attributes) for feature in page.features)
        if not page.exceededTransferLimit or not page.features:
            return rows
        offset += len(page.features)


def confirm_portid(client: httpx.Client, name_prefix: str) -> list[tuple[str, str]]:
    """Every (portid, portname) whose name starts with the prefix."""
    page = _query(
        client,
        {
            "where": f"portname LIKE '{name_prefix}%'",
            "outFields": "portid,portname",
            "returnDistinctValues": "true",
            "returnGeometry": "false",
            "f": "json",
        },
    )
    pairs = {
        (feature.attributes.portid, feature.attributes.portname)
        for feature in page.features
        if feature.attributes.portid is not None and feature.attributes.portname is not None
    }
    return sorted(pairs)


def upsert_portwatch(conn: psycopg.Connection[TupleRow], rows: Sequence[PortwatchRow]) -> int:
    """Write every row, replacing whatever was there for that day and port."""
    for row in rows:
        _ = conn.execute(
            UPSERT,
            (
                row.date,
                row.portid,
                row.portcalls,
                row.portcalls_tanker,
                row.export_tanker_t,
                row.import_tanker_t,
            ),
        )
    return len(rows)


def _query(client: httpx.Client, params: dict[str, str]) -> _Page:
    response = client.get(ENDPOINT, params=params)
    _ = response.raise_for_status()
    return PAGE_ADAPTER.validate_python(response.json())


def _as_row(attributes: _Attributes) -> PortwatchRow:
    if attributes.date is None or attributes.portid is None:
        raise ValueError(f"PortWatch returned a record with no date or portid: {attributes}")
    return PortwatchRow(
        date=_as_date(attributes.date),
        portid=attributes.portid,
        portcalls=_rounded(attributes.portcalls),
        portcalls_tanker=_rounded(attributes.portcalls_tanker),
        export_tanker_t=_rounded(attributes.export_tanker),
        import_tanker_t=_rounded(attributes.import_tanker),
    )


def _as_date(value: int | str) -> date:
    """Epoch milliseconds or an ISO date; the service has used both."""
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1000, tz=UTC).date()
    return date.fromisoformat(value)


def _rounded(value: float | None) -> int | None:
    return None if value is None else round(value)
