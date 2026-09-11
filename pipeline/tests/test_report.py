"""What the milestone 1 check accepts and what it refuses."""

from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb

from oiltracker.ports.config import PortConfig, Terminal, load_port, seed_circle
from oiltracker.ports.store import upsert_terminals
from oiltracker.report.m1 import TOTAL, M1Row, m1_check, m1_rows, render

EXITED = datetime(2024, 3, 5, 12, tzinfo=UTC)


def _seeded(cfg: PortConfig) -> list[Terminal]:
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


def _insert_event(
    conn: psycopg.Connection[TupleRow], port_id: str, terminal_id: str, index: int
) -> None:
    """One berth visit and the export event that references it."""
    exited = EXITED + timedelta(hours=index)
    row = conn.execute(
        "insert into berth_visits (port, mmsi, terminal_id, polygon_version, entered_at,"
        " exited_at, dwell_hours, low_speed_fraction, observations, open_ended, code_version)"
        " values (%s, %s, %s, 0, %s, %s, 8, 1.0, 96, false, 'test') returning id",
        (port_id, 367000000 + index, terminal_id, exited - timedelta(hours=8), exited),
    ).fetchone()
    assert row is not None
    _ = conn.execute(
        "insert into export_events (berth_visit_id, port, mmsi, size_class,"
        " size_class_source, terminal_id, polygon_version, entered_at, exited_at,"
        " gate_crossed_at, hours_to_gate, gate_interpolated, departure_draught_candidates,"
        " code_version) values (%s, %s, %s, 'vlcc', 'estimated', %s, 0, %s, %s, %s, 1.0,"
        " false, %s, 'test')",
        (
            int(row[0]),
            port_id,
            367000000 + index,
            terminal_id,
            exited - timedelta(hours=8),
            exited,
            exited + timedelta(hours=1),
            Jsonb([]),
        ),
    )


def total(month: str, events: int, calls: int, no_departure: int = 0) -> M1Row:
    return M1Row(
        month=month,
        terminal_id=TOTAL,
        export_events=events,
        visits_without_departure=no_departure,
        portwatch_tanker_calls=calls,
        ratio=events / calls if calls else None,
    )


def steady(months: int = 12, events: int = 40, calls: int = 120) -> list[M1Row]:
    return [total(f"2023-{index + 1:02d}", events, calls) for index in range(months)]


def test_a_stable_series_above_the_floor_passes() -> None:
    assert m1_check(steady()) == []


def test_a_month_far_below_the_median_ratio_fails() -> None:
    rows = steady()
    # 30 percent of the median ratio, while still above the event floor.
    rows[5] = total("2023-06", 40, 400)

    problems = m1_check(rows)
    assert any("2023-06" in problem and "deviates" in problem for problem in problems)


def test_a_month_with_no_events_but_real_calls_fails() -> None:
    rows = steady()
    rows[3] = total("2023-04", 0, 120)

    problems = m1_check(rows)
    assert any("2023-04" in problem and "no export events" in problem for problem in problems)


def test_a_month_under_the_event_floor_fails() -> None:
    rows = steady()
    rows[7] = total("2023-08", 12, 120)

    problems = m1_check(rows)
    assert any("2023-08" in problem and "under the floor" in problem for problem in problems)


def test_a_month_with_no_calls_has_no_ratio_and_is_not_faulted_for_it() -> None:
    rows = steady()
    rows[2] = total("2023-03", 40, 0)

    problems = m1_check(rows)
    assert not any("2023-03" in problem and "deviates" in problem for problem in problems)


def test_only_total_rows_are_checked() -> None:
    """A per-terminal row, however poor, is reported but never checked."""
    rows = [
        *steady(),
        M1Row("2023-01", "flint_hills_ingleside", 1, 0, 120, 1 / 120),
    ]
    assert m1_check(rows) == []


def test_render_lists_every_row_with_its_month() -> None:
    rendered = render([total("2023-01", 40, 120), M1Row("2023-01", "enbridge", 30, 2, 120, 0.25)])

    assert "2023-01" in rendered
    assert TOTAL in rendered
    assert "enbridge" in rendered
    assert "0.333" in rendered


def test_flint_hills_events_are_reported_but_stay_out_of_the_crude_total(
    db: psycopg.Connection[TupleRow],
) -> None:
    """Flint Hills is crude: uncertain, so its events never join the total."""
    cfg = load_port("corpus_christi")
    _ = upsert_terminals(db, cfg, _seeded(cfg))

    for terminal_id, count in (
        ("enbridge_ingleside", 3),
        ("south_texas_gateway", 2),
        ("flint_hills_ingleside", 5),
    ):
        for index in range(count):
            _insert_event(db, cfg.id, terminal_id, index)

    _ = db.execute(
        "insert into portwatch_daily (date, portid, portcalls_tanker, fetched_at)"
        " values ('2024-03-05', %s, 10, now())"
        " on conflict (date, portid) do update set portcalls_tanker = excluded.portcalls_tanker",
        (cfg.portwatch_portid,),
    )

    rows = {(row.month, row.terminal_id): row for row in m1_rows(db, cfg)}

    assert rows[("2024-03", "flint_hills_ingleside")].export_events == 5
    # Three plus two, not ten: the five uncertain ones are listed, never summed.
    assert rows[("2024-03", TOTAL)].export_events == 5
    # Calls are summed over the whole month, so this is at least the day added here.
    assert rows[("2024-03", TOTAL)].portwatch_tanker_calls >= 10
