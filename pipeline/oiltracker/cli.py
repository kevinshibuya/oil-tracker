"""The `oil` command line: one Typer sub-app per stage of the pipeline."""

import sys
from datetime import date
from pathlib import Path
from typing import Annotated

import httpx
import typer

from oiltracker.ais.backfill import backfill, backfill_day, parquet_path
from oiltracker.ais.fixture import make_fixture
from oiltracker.ais.load import load_day, loadable_days
from oiltracker.ais.probe import probe_year
from oiltracker.ais.sources import CANDIDATES, SourceSpec, load_sources, write_sources
from oiltracker.db.connect import connect
from oiltracker.db.migrate import migrate
from oiltracker.events.run import detect_port
from oiltracker.paths import AIS_SOURCES, DATA_DIR, FIXTURES_DIR, VESSELS_DIR
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
from oiltracker.version import code_version
from oiltracker.vessels.build import build_vessels

MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"
DEFAULT_YEARS = [2023, 2024, 2025]
DEFAULT_PORT = "corpus_christi"
SPAN_START = "2023-01-01"
SPAN_END = "2025-09-30"
FIXTURE_MAX_BYTES = 4 * 1024 * 1024
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


FromOption = Annotated[str, typer.Option("--from", help="First day, ISO format.")]
ToOption = Annotated[str, typer.Option("--to", help="Last day, inclusive, ISO format.")]


@ais_app.command("backfill")
def ais_backfill(
    port: PortOption = DEFAULT_PORT,
    from_day: FromOption = SPAN_START,
    to_day: ToOption = SPAN_END,
    workers: Annotated[int, typer.Option("--workers", help="Days fetched at once.")] = 2,
    data_dir: Annotated[Path, typer.Option("--data-dir")] = DATA_DIR,
) -> None:
    """Fetch every day in the span that the manifest does not already account for."""
    cfg = load_port(port)
    counts = backfill(
        Settings(),
        cfg,
        load_sources(AIS_SOURCES),
        date.fromisoformat(from_day),
        date.fromisoformat(to_day),
        data_dir,
        workers,
    )
    if not counts:
        typer.echo("nothing to do: every day in the span is already accounted for")
        return
    for status, count in sorted(counts.items()):
        typer.echo(f"{status}: {count}")
    if counts.get("failed"):
        raise typer.Exit(code=1)


@ais_app.command("fixture")
def ais_fixture(
    port: PortOption = DEFAULT_PORT,
    day: Annotated[str, typer.Option("--day", help="The day to cut down.")] = "2024-03-05",
    data_dir: Annotated[Path, typer.Option("--data-dir")] = DATA_DIR,
) -> None:
    """Cut one real day down to a parquet small enough to commit."""
    cfg = load_port(port)
    when = date.fromisoformat(day)
    source = parquet_path(data_dir, cfg.id, when)

    if not source.exists():
        with connect(Settings()) as conn:
            row = backfill_day(conn, cfg, load_sources(AIS_SOURCES)[when.year], when, data_dir)
        if row.status != "done":
            typer.echo(f"blocked: {day} is {row.status}, not done")
            raise typer.Exit(code=1)

    out = FIXTURES_DIR / "ais" / cfg.id / f"{when.isoformat()}.parquet"
    rows = make_fixture(source, out, FIXTURE_MAX_BYTES)
    typer.echo(f"{out} holds {rows} rows in {out.stat().st_size} bytes")


@ais_app.command("load")
def ais_load(
    port: PortOption = DEFAULT_PORT,
    from_day: FromOption = SPAN_START,
    to_day: ToOption = SPAN_END,
    force: Annotated[bool, typer.Option("--force", help="Reload days already loaded.")] = False,
    data_dir: Annotated[Path, typer.Option("--data-dir")] = DATA_DIR,
) -> None:
    """Load every fetched day's tanker positions and statics into PostGIS."""
    cfg = load_port(port)
    start, end = date.fromisoformat(from_day), date.fromisoformat(to_day)

    with connect(Settings()) as conn:
        days = loadable_days(conn, cfg, start, end, force)
        if not days:
            typer.echo("nothing to load: every fetched day in the span is already loaded")
            return
        total = 0
        for index, day in enumerate(days, start=1):
            rows = load_day(conn, cfg, day, data_dir)
            conn.commit()
            total += rows
            print(f"[{index}/{len(days)}] {day} {rows} rows", file=sys.stderr, flush=True)
    typer.echo(f"loaded {total} tanker positions across {len(days)} days")


@vessels_app.command("build")
def vessels_build(
    port: PortOption = DEFAULT_PORT,
    particulars: Annotated[Path, typer.Option("--particulars")] = VESSELS_DIR / "particulars.csv",
) -> None:
    """Rebuild the vessels table from the day statics and positions."""
    cfg = load_port(port)
    with connect(Settings()) as conn:
        written = build_vessels(conn, particulars, code_version(), cfg.id)
        conn.commit()
    typer.echo(f"wrote {written} vessels")


@events_app.command("detect")
def events_detect(port: PortOption = DEFAULT_PORT) -> None:
    """Rebuild berth visits and export events from the loaded positions."""
    cfg = load_port(port)
    with connect(Settings()) as conn:
        visits, events = detect_port(conn, cfg, code_version())
        conn.commit()
    typer.echo(f"{visits} berth visits, {events} export events")
