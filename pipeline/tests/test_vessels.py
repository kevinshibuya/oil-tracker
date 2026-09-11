# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false
#
# pyarrow's stubs erase element types; see oiltracker.ais.readers.
"""Loading a real day of tankers, and rebuilding the vessels table from it."""

from datetime import date
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
import pytest
from psycopg.rows import TupleRow

from oiltracker.ais.load import is_tanker, load_day, tanker_mask
from oiltracker.paths import FIXTURES_DIR
from oiltracker.ports.config import PortConfig, load_port
from oiltracker.vessels.build import build_vessels

FIXTURE_DAY = date(2024, 3, 5)
CODE_VERSION = "testcommit"

CURATED_HEADER = "imo,mmsi,name,size_class,dwt_t,tpc_t_per_cm,scantling_draught_m,source_url"


@pytest.fixture
def cfg() -> PortConfig:
    return load_port("corpus_christi")


def fixture_tanker_mmsis() -> set[int]:
    table = pq.read_table(FIXTURES_DIR / "ais" / "corpus_christi" / f"{FIXTURE_DAY}.parquet")
    mmsis = table.filter(tanker_mask(table)).column("mmsi").to_pylist()
    return {int(mmsi) for mmsi in mmsis if mmsi is not None}


@pytest.mark.parametrize(
    ("vessel_type", "cargo", "expected"),
    [
        (None, 82, True),
        (80, None, True),
        (89, None, True),
        (None, None, False),
        (70, None, False),
        (None, 79, False),
        (90, 90, False),
    ],
)
def test_is_tanker(vessel_type: int | None, cargo: int | None, expected: bool) -> None:
    assert is_tanker(vessel_type, cargo) is expected


def test_load_day_writes_one_statics_row_per_tanker_mmsi(
    db: psycopg.Connection[TupleRow], cfg: PortConfig
) -> None:
    expected = fixture_tanker_mmsis()
    loaded = load_day(db, cfg, FIXTURE_DAY, FIXTURES_DIR)

    assert loaded > 0
    statics = db.execute(
        "select mmsi from vessel_statics_daily where port = %s and day = %s",
        (cfg.id, FIXTURE_DAY),
    ).fetchall()
    assert {row[0] for row in statics} == expected

    positions = db.execute("select count(*) from positions where port = %s", (cfg.id,)).fetchone()
    assert positions is not None
    assert positions[0] == loaded


def test_build_vessels_covers_every_tanker_and_honours_the_curated_csv(
    db: psycopg.Connection[TupleRow], cfg: PortConfig, tmp_path: Path
) -> None:
    expected = fixture_tanker_mmsis()
    _ = load_day(db, cfg, FIXTURE_DAY, FIXTURES_DIR)

    chosen = sorted(expected)[0]
    curated = tmp_path / "particulars.csv"
    curated.write_text(
        f"{CURATED_HEADER}\n"
        f"9999999,{chosen},CURATED TESTER,vlcc,300000,180.5,22.5,https://example.invalid/record\n"
    )

    written = build_vessels(db, curated, CODE_VERSION, cfg.id)
    assert written == len(expected)

    rows = db.execute("select mmsi from vessels").fetchall()
    assert {row[0] for row in rows} == expected

    row = db.execute(
        "select name, size_class, size_class_source, dwt_t, tpc_t_per_cm,"
        " scantling_draught_m, particulars_source, imo, code_version"
        " from vessels where mmsi = %s",
        (chosen,),
    ).fetchone()
    assert row is not None
    name, cls, cls_source, dwt, tpc, scantling, particulars_source, imo, version = row
    assert name == "CURATED TESTER"
    assert cls == "vlcc"
    assert cls_source == "curated"
    assert dwt == pytest.approx(300000)
    assert tpc == pytest.approx(180.5)
    assert scantling == pytest.approx(22.5)
    assert particulars_source == "curated"
    assert imo == 9999999
    assert version == CODE_VERSION


def test_uncurated_vessels_are_marked_estimated(
    db: psycopg.Connection[TupleRow], cfg: PortConfig, tmp_path: Path
) -> None:
    _ = load_day(db, cfg, FIXTURE_DAY, FIXTURES_DIR)
    empty = tmp_path / "particulars.csv"
    empty.write_text(CURATED_HEADER + "\n")

    _ = build_vessels(db, empty, CODE_VERSION, cfg.id)

    sources = db.execute("select distinct size_class_source from vessels").fetchall()
    assert {row[0] for row in sources} == {"estimated"}
