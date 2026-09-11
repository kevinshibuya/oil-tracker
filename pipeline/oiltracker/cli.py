"""The `oil` command line: one Typer sub-app per stage of the pipeline."""

from pathlib import Path
from typing import Annotated

import httpx
import typer

from oiltracker.ais.probe import probe_year
from oiltracker.ais.sources import CANDIDATES, SourceSpec, write_sources
from oiltracker.db.connect import connect
from oiltracker.db.migrate import migrate
from oiltracker.paths import AIS_SOURCES
from oiltracker.ports.check import check_port
from oiltracker.ports.config import (
    Terminal,
    load_port,
    read_terminals_geojson,
    seed_circle,
    terminals_path,
    write_terminals_geojson,
)
from oiltracker.ports.store import upsert_terminals
from oiltracker.settings import Settings

MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"
DEFAULT_YEARS = [2023, 2024, 2025]
DEFAULT_PORT = "corpus_christi"
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


PortOption = Annotated[str, typer.Option("--port", help="Port id in config/ports.")]


@port_app.command("seed")
def port_seed(port: PortOption = DEFAULT_PORT) -> None:
    """Write the version-0 seed circles into the port's geojson of record."""
    cfg = load_port(port)
    path = terminals_path(cfg)
    kept = [t for t in read_terminals_geojson(path) if t.version != 0] if path.exists() else []
    seeded = [
        Terminal(
            terminal_id=seed.terminal_id,
            name=seed.name,
            crude=seed.crude,
            version=0,
            geom=seed_circle(seed.centre[0], seed.centre[1], cfg.seed_radius_m),
        )
        for seed in cfg.terminal_seeds
    ]
    write_terminals_geojson(path, kept + seeded)
    typer.echo(f"wrote {len(seeded)} version-0 features to {path} ({len(kept)} kept)")


@port_app.command("load")
def port_load(port: PortOption = DEFAULT_PORT) -> None:
    """Upsert every polygon version in the geojson into the terminals table."""
    cfg = load_port(port)
    terminals = read_terminals_geojson(terminals_path(cfg))
    with connect(Settings()) as conn:
        written = upsert_terminals(conn, cfg, terminals)
        conn.commit()
    versions = sorted({t.version for t in terminals})
    typer.echo(f"upserted {written} terminals at versions {versions}")


@port_app.command("check")
def port_check(port: PortOption = DEFAULT_PORT) -> None:
    """Report every problem in the port's geometry. Exits 1 when there is one."""
    cfg = load_port(port)
    problems = check_port(cfg, read_terminals_geojson(terminals_path(cfg)))
    for problem in problems:
        typer.echo(problem)
    if problems:
        raise typer.Exit(code=1)
    typer.echo("ok")
