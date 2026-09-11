"""Detection over the real fixture day, end to end through the database."""

import psycopg
from psycopg.rows import TupleRow

from oiltracker.ais.load import load_day
from oiltracker.events.run import detect_port
from oiltracker.paths import FIXTURES_DIR, VESSELS_DIR
from oiltracker.ports.config import PortConfig, Terminal, load_port, seed_circle
from oiltracker.ports.store import upsert_terminals
from oiltracker.vessels.build import build_vessels
from tests.test_vessels import CODE_VERSION, FIXTURE_DAY


def seeded_terminals(cfg: PortConfig) -> list[Terminal]:
    return [
        Terminal(
            terminal_id=seed.terminal_id,
            name=seed.name,
            crude=seed.crude,
            version=0,
            geom=seed_circle(seed.centre[0], seed.centre[1], cfg.seed_radius_m),
        )
        for seed in cfg.terminal_seeds
    ]


def test_detection_runs_over_a_real_day(db: psycopg.Connection[TupleRow]) -> None:
    cfg = load_port("corpus_christi")
    _ = upsert_terminals(db, cfg, seeded_terminals(cfg))
    _ = load_day(db, cfg, FIXTURE_DAY, FIXTURES_DIR)
    _ = build_vessels(db, VESSELS_DIR / "particulars.csv", CODE_VERSION, cfg.id)

    visits, events = detect_port(db, cfg, CODE_VERSION)

    assert visits >= 0
    assert events <= visits

    orphans = db.execute(
        "select count(*) from export_events e"
        " left join berth_visits v on v.id = e.berth_visit_id where v.id is null"
    ).fetchone()
    assert orphans is not None
    assert orphans[0] == 0

    versions = db.execute(
        "select distinct polygon_version from berth_visits where port = %s", (cfg.id,)
    ).fetchall()
    assert {row[0] for row in versions} <= {0}

    missing_class = db.execute(
        "select count(*) from export_events where port = %s and size_class is null", (cfg.id,)
    ).fetchone()
    assert missing_class is not None
    assert missing_class[0] == 0
