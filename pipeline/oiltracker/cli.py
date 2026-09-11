"""The `oil` command line: one Typer sub-app per stage of the pipeline."""

from pathlib import Path

import typer

from oiltracker.db.connect import connect
from oiltracker.db.migrate import migrate
from oiltracker.settings import Settings

MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"

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
