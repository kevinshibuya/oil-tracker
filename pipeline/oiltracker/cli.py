"""The `oil` command line: one Typer sub-app per stage of the pipeline."""

from pathlib import Path
from typing import Annotated

import httpx
import typer

from oiltracker.ais.probe import probe_year
from oiltracker.ais.sources import CANDIDATES, SourceSpec, write_sources
from oiltracker.db.connect import connect
from oiltracker.db.migrate import migrate
from oiltracker.settings import Settings

MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"
REPO_ROOT = Path(__file__).resolve().parents[2]
AIS_SOURCES = REPO_ROOT / "config" / "ais_sources.yaml"
DEFAULT_YEARS = [2023, 2024, 2025]
PROBE_TIMEOUT_S = 60.0

app = typer.Typer(no_args_is_help=True, help="Crude oil export tracking for Corpus Christi.")

db_app = typer.Typer(no_args_is_help=True, help="Schema migrations.")
ais_app = typer.Typer(no_args_is_help=True, help="AIS archive probing, backfill and loading.")
port_app = typer.Typer(no_args_is_help=True, help="Port configuration and terminal polygons.")
vessels_app = typer.Typer(no_args_is_help=True, help="Vessel identity, class and particulars.")
events_app = typer.Typer(no_args_is_help=True, help="Berth visits and export events.")
truth_app = typer.Typer(no_args_is_help=True, help="External truth series.")
report_app = typer.Typer(no_args_is_help=True, help="Milestone acceptance reports.")

app.add_typer(db_app, name="db")
app.add_typer(ais_app, name="ais")
app.add_typer(port_app, name="port")
app.add_typer(vessels_app, name="vessels")
app.add_typer(events_app, name="events")
app.add_typer(truth_app, name="truth")
app.add_typer(report_app, name="report")


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply every migration not yet recorded, in file-name order."""
    settings = Settings()
    with connect(settings) as conn:
        applied = migrate(conn, MIGRATIONS_DIR)
        conn.commit()
    for name in applied:
        typer.echo(name)
    if not applied:
        typer.echo("no migrations to apply")


@ais_app.command("probe")
def ais_probe(
    years: Annotated[
        list[int],
        typer.Option("--years", help="Year to probe; repeat the flag for several."),
    ] = DEFAULT_YEARS,
    out: Annotated[
        Path, typer.Option("--out", help="Where to write the source list.")
    ] = AIS_SOURCES,
) -> None:
    """Find which archives serve each year and record their headers."""
    sources: dict[int, list[SourceSpec]] = {}
    with httpx.Client(timeout=PROBE_TIMEOUT_S) as client:
        for year in years:
            specs = probe_year(year, CANDIDATES, client)
            if not specs:
                typer.echo(f"blocked: no source for {year}")
                raise typer.Exit(code=1)
            for spec in specs:
                typer.echo(f"{year} {spec.format} {spec.url_pattern}")
                typer.echo(f"  header: {','.join(spec.header)}")
            sources[year] = specs

    out.parent.mkdir(parents=True, exist_ok=True)
    write_sources(out, sources)
    typer.echo(f"wrote {out}")
