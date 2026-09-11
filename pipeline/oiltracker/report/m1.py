"""The milestone 1 acceptance report: export events per month against PortWatch.

The report is the gate. It does not tune anything; when a month looks wrong it
says so and `--check` exits non-zero, because a low month means missed events,
not a quiet port.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

import psycopg
from psycopg.rows import TupleRow

from oiltracker.ports.config import PortConfig

TOTAL = "total"

RATIO_DEVIATION = 0.5
"""How far a month's ratio may sit from the median of the monthly ratios."""

MIN_EVENTS_PER_MONTH = 20
"""Enbridge Ingleside alone reports about a million barrels a day, which at 0.7
to 1.5 million barrels a departure is more than twenty departures a month. A
lower count means the pipeline missed events."""

ROWS = """
with months as (
    select generate_series(
        date_trunc('month', %(start)s::date),
        date_trunc('month', %(end)s::date),
        interval '1 month')::date as month
), terminals_now as (
    select terminal_id, crude from terminals
    where port = %(port)s
      and version = (select max(version) from terminals where port = %(port)s)
), events as (
    select date_trunc('month', exited_at)::date as month, terminal_id, count(*) as n
    from export_events where port = %(port)s group by 1, 2
), no_departure as (
    select date_trunc('month', v.exited_at)::date as month, v.terminal_id, count(*) as n
    from berth_visits v
    left join export_events e on e.berth_visit_id = v.id
    where v.port = %(port)s and e.id is null
    group by 1, 2
), calls as (
    select date_trunc('month', date)::date as month, sum(portcalls_tanker) as n
    from portwatch_daily where portid = %(portid)s group by 1
)
select m.month, t.terminal_id, t.crude,
       coalesce(e.n, 0), coalesce(d.n, 0), coalesce(c.n, 0)
from months m
cross join terminals_now t
left join events e on e.month = m.month and e.terminal_id = t.terminal_id
left join no_departure d on d.month = m.month and d.terminal_id = t.terminal_id
left join calls c on c.month = m.month
order by m.month, t.terminal_id
"""


@dataclass(frozen=True)
class M1Row:
    """One month for one terminal, or the month's crude total."""

    month: str
    terminal_id: str
    export_events: int
    visits_without_departure: int
    portwatch_tanker_calls: int
    ratio: float | None


def m1_rows(conn: psycopg.Connection[TupleRow], cfg: PortConfig) -> list[M1Row]:
    """One row per month and terminal, plus a crude-only total row per month."""
    raw = conn.execute(
        ROWS,
        {
            "port": cfg.id,
            "portid": cfg.portwatch_portid,
            "start": "2023-01-01",
            "end": "2025-09-01",
        },
    ).fetchall()

    rows: list[M1Row] = []
    totals: dict[str, tuple[int, int, int]] = {}

    for month, terminal_id, crude, events, no_departure, calls in raw:
        label = str(month)[:7]
        rows.append(
            M1Row(
                month=label,
                terminal_id=str(terminal_id),
                export_events=int(events),
                visits_without_departure=int(no_departure),
                portwatch_tanker_calls=int(calls),
                ratio=_ratio(int(events), int(calls)),
            )
        )
        if crude == "yes":
            # The total sums only the terminals known to move crude.
            seen = totals.get(label, (0, 0, int(calls)))
            totals[label] = (
                seen[0] + int(events),
                seen[1] + int(no_departure),
                int(calls),
            )

    for label, (events, no_departure, calls) in sorted(totals.items()):
        rows.append(
            M1Row(
                month=label,
                terminal_id=TOTAL,
                export_events=events,
                visits_without_departure=no_departure,
                portwatch_tanker_calls=calls,
                ratio=_ratio(events, calls),
            )
        )
    return sorted(rows, key=lambda row: (row.month, row.terminal_id == TOTAL, row.terminal_id))


def m1_check(rows: Sequence[M1Row]) -> list[str]:
    """Every month whose crude total does not look like a working port."""
    totals = [row for row in rows if row.terminal_id == TOTAL]
    ratios = [row.ratio for row in totals if row.ratio is not None]
    median = statistics.median(ratios) if ratios else None

    problems: list[str] = []
    for row in totals:
        if row.export_events == 0 and row.portwatch_tanker_calls > 0:
            problems.append(
                f"{row.month}: no export events against {row.portwatch_tanker_calls} tanker calls"
            )
            continue
        if row.export_events < MIN_EVENTS_PER_MONTH:
            problems.append(
                f"{row.month}: {row.export_events} export events, under the floor of "
                f"{MIN_EVENTS_PER_MONTH}"
            )
        if median and row.ratio is not None and abs(row.ratio - median) / median > RATIO_DEVIATION:
            problems.append(
                f"{row.month}: ratio {row.ratio:.3f} deviates more than "
                f"{RATIO_DEVIATION:.0%} from the median {median:.3f}"
            )
    return problems


def render(rows: Sequence[M1Row]) -> str:
    """A fixed-width table, months ascending, the total last within each month."""
    header = f"{'month':<8} {'terminal':<24} {'events':>7} {'no dep':>7} {'calls':>7} {'ratio':>7}"
    lines = [header, "-" * len(header)]
    for row in rows:
        ratio = f"{row.ratio:.3f}" if row.ratio is not None else "-"
        lines.append(
            f"{row.month:<8} {row.terminal_id:<24} {row.export_events:>7} "
            f"{row.visits_without_departure:>7} {row.portwatch_tanker_calls:>7} {ratio:>7}"
        )
    return "\n".join(lines)


def _ratio(events: int, calls: int) -> float | None:
    return events / calls if calls else None
