# Oil tracker

A Python pipeline that turns NOAA ship-position data into a record of crude tankers loading at the
Port of Corpus Christi, Texas, as the first step toward a weekly estimate of the port's crude
exports.

**Status: work in progress.** This is an early-stage project: only milestone 1, ingest, is
built, with its code and 90 tests committed. The full-data runs from the positions load onward have
not happened yet, and the export estimate, the satellite signals and the globe exist only as design.

## What it is

The question is how many barrels of crude left Corpus Christi this week. Official numbers arrive
late. Census port-level exports come about five weeks late, and the weekly EIA figure covers the
whole country, not one port. Corpus Christi handles about half of US crude exports, so the gap
matters.

Ships report themselves. Every large vessel broadcasts AIS, a radio beacon with its position, speed,
type, dimensions and a crew-typed draught. A crude tanker that sits slowly at an Ingleside crude
berth for hours and then sails out past the Aransas Pass jetties has loaded a cargo. Its size class
bounds how much. Count those departures and size them, and you have an export estimate days or weeks
before customs data publishes.

The target is weekly barrels loaded at two terminals, Enbridge Ingleside Energy Center and South
Texas Gateway. The source is NOAA MarineCadastre's free daily archive of one-minute positions in US
waters. The aim is a nowcast. Each week gets an estimate fixed before the official figure exists,
and later it is scored against that figure.

## Where it stands

Milestone 1 turns three years of AIS into a PostGIS store. What it produces:

- **A filtered AIS extract.** `oil ais backfill` streams NOAA's national daily files, keeps the rows
  inside the port's bounding box, latitude 27.55 to 28.05 and longitude -97.60 to -96.60, and writes
  one parquet file per day. The span is 1,004 days, 2023-01-01 to 2025-09-30. Daily files are 0.2 to
  0.35 GB compressed, about 250 GB in all, and none is stored whole.
- **Tanker positions and vessels.** `oil ais load` copies tanker rows (AIS type or cargo code 80 to
  89) into a `positions` table with 33 monthly partitions. `oil vessels build` gives each tanker a
  size class (MR, Panamax/LR1, Aframax, Suezmax, VLCC or unknown) from its length and beam, and
  estimated deadweight and tonnes per centimetre.
- **Terminal polygons.** Three terminals start as 700 m seed circles around their published centres,
  committed in `config/ports/corpus_christi.terminals.geojson`. `oil port derive` replaces them with
  polygons drawn from where tankers actually sit still.
- **Berth visits and export events.** `oil events detect` runs a state machine over each tanker's
  track. A visit is at least 4 hours inside a polygon with at least 80 percent of observations under
  0.5 knots. An export event is a visit followed by a crossing of the 2,972 m gate line across the
  Aransas Pass jetties. Each event records the arrival draught and every candidate departure
  draught. The cargo columns exist and stay empty until milestone 3.
- **A port-call cross-check.** `oil truth portwatch` loads IMF PortWatch daily port calls. Its live
  run stored 1,343 days for Corpus Christi, port id `port264`, with 1,004 of them in the span,
  carrying 4,098 tanker port calls, 4.08 a day.
- **An acceptance report.** `oil report m1 --check` compares monthly export events with PortWatch
  tanker calls. It fails any month under 20 events, any month with zero events and some calls, and
  any month whose ratio strays more than 50 percent from the median.

The tests run on synthetic tracks and on one committed day of real data,
`pipeline/tests/fixtures/ais/corpus_christi/2024-03-05.parquet`. It holds every tanker row from that
day plus a fixed 10 percent of other vessels, 2.7 MB.

Not done yet. The positions load, the vessels build, the polygon derivation, the event detection and
the acceptance report have code and tests but have not run on the full three years, so the committed
geojson still holds the version-0 seed circles. The curated vessel table ships as a header only, for
the reason given under the decisions below.

### Designed, not built

From `docs/design.md`, in milestone order:

1. A web globe on MapLibre GL and deck.gl, with tracks, a time slider, and ship and terminal panels.
2. The nowcast: Census and port-tonnage truth loaders, the cargo estimate per event, calibration and
   a rolling-origin scoreboard.
3. Tank fill classes from Sentinel-2 imagery of the floating-roof tank farms.
4. A Sentinel-1 radar check of AIS completeness at berth and anchorage.
5. A live mode from a streaming AIS feed, and a panel that explains a missed week from cited
   sources.

## How it's built

```mermaid
flowchart LR
    noaa["NOAA AIS daily files<br/>1,004 days, about 250 GB"] --> backfill["ais backfill<br/>stream and filter to port box"]
    backfill --> parquet[("day parquet<br/>data/ais/")]
    parquet -->|ais load, tankers only| pos[("positions<br/>vessel_statics_daily")]
    pos -->|vessels build| vessels[("vessels")]
    seeds["terminals geojson<br/>seed circles"] -->|port load| terms[("terminals")]
    pos -->|port derive| terms
    pos --> detect["events detect<br/>state machine"]
    terms --> detect
    vessels --> detect
    detect --> events[("berth_visits<br/>export_events")]
    pw["IMF PortWatch API"] -->|truth portwatch| pwd[("portwatch_daily")]
    events --> report["report m1"]
    pwd --> report
```

| Path | What lives there |
| --- | --- |
| `pipeline/oiltracker/ais/` | Archive probe, streaming backfill, readers, positions load, test fixture builder |
| `pipeline/oiltracker/ports/` | Port config, seed circles, polygon derivation, geometry checks |
| `pipeline/oiltracker/vessels/` | Size classes and estimated particulars |
| `pipeline/oiltracker/events/` | Berth-visit state machine, gate crossings, draught plateaus |
| `pipeline/oiltracker/truth/`, `report/` | PortWatch loader and the milestone 1 report |
| `pipeline/oiltracker/db/migrations/` | Plain SQL migrations applied by `oil db migrate` |
| `pipeline/tests/` | 13 test files and the one-day AIS fixture |
| `config/` | Per-year AIS sources, port and terminal config, curated vessel table |
| `infra/docker-compose.yml` | PostGIS 16 with PostGIS 3.4 |
| `docs/` | Design, glossary, milestone 1 plan, research notes |

## Why it's built this way

**A nowcast, not a forecast, with each week fixed at a cutoff.**
Ships show what was loaded, not what will be produced or sold next month, so the horizon is the
current week and well production is a stated non-goal. Each week's estimate is frozen at a stated
cutoff before the truth publishes. Development uses 2023 and 2024 and leaves 2025 untouched until
the end. Scoring is rolling-origin: month m is scored with parameters fitted only on months before
m. ([design](docs/design.md#target-and-truth), [q13
notes](docs/research/q13-leg-a.md#4-calibration-without-leakage))

**Truth is the Census port-level export series, cross-checked against PortWatch.**
The Census API gives monthly crude exports for port 5311 under HS 2709, in kilograms, about five
weeks late. Kpler and Vortexa have no free tier and UNCTAD publishes only quarterly aggregates, so
none is used. Census arrives in milestone 3 with calibration, and milestone 1 checks itself against
PortWatch tanker port calls only. The cost is that port 5311 covers more berths than the two
Ingleside terminals. If the fitted scalar drifts above about 1.05, the plan is to add berths, not
let the scalar hide the gap. ([design](docs/design.md#target-and-truth), [data
sources](docs/research/data-sources.md#ground-truth))

**Terminal polygons come from tanker positions, not a hand-drawn map.**
The original plan digitised berths by hand. Plan review changed that because the agent executing the
plan has no GUI. `oil port derive` takes every position under 0.5 knots within 700 m of a terminal's
published centre, draws a concave hull, buffers it by 100 m and splits any shared water by Voronoi
cell, so it goes to the nearer centre. That rule exists because the South Texas Gateway and Flint
Hills seed circles overlap by about 416 m. A seed with fewer than 100 points keeps its circle and
says so. A check of the derived polygons against satellite imagery is planned and has not run.
([design](docs/design.md#export-event), [plan, task 9](docs/plans/2026-09-11-m1-ingest.md))

**Cargo comes from a load factor first, and from draught only if draught earns it.**
The obvious method, departure draught minus arrival draught times tonnes per centimetre, is wrong
here. A tanker pumps out ballast while it loads, so the hull barely sinks. On a VLCC that goes from
10.0 m to 15.5 m, the delta gives 97,900 t, about half the real cargo. Two independent write-ups of
the problem disagreed: one wanted a deadweight-scale method reading draught down from the deepest
legal line, the other a plain load factor, since draught quality alone does not prove cargo
accuracy. The design keeps both on every event. The shipped baseline is estimated deadweight times a
per-class factor fitted on training months. The deadweight scale switches on only if it beats that
out of sample. ([design](docs/design.md#cargo-per-event-the-q13-method), [first
write-up](docs/research/q13-leg-a.md), [second write-up](docs/research/q13-leg-b.md))

**Vessel particulars come from a small curated table, and everything else is estimated.**
The budget is zero, so there is no paid ship registry. Deadweight and tonnes per centimetre come
from AIS length and beam with class-typical coefficients, roughly plus or minus 10 percent when the
class is right and 25 percent when it is wrong. On a VLCC partial load a 10 percent deadweight error
becomes a 15 percent cargo error, so the frequent VLCCs are the ones to curate first. Today the
table is a header only. ABS Record serves a page shell with no public data behind it, the DNV
register path returns 404 and LR Class Direct needs a sign-in, so every vessel is marked `estimated`
rather than given invented numbers. ([design](docs/design.md#data-sources), [q13
notes](docs/research/q13-leg-a.md#2-dwt-and-tpc-from-length-and-width))

**The backfill streams national files and keeps only the port.**
Three years is about 250 GB of daily files. An earlier note put each file at 3.1 GB, and measured
they are 0.2 to 0.35 GB. The downloader streams each file through a bounding-box filter, never
stores a national file, keeps each worker under 2 GB of memory, and records every day in a manifest
so an interrupted run resumes. NOAA keeps a rolling ten-year window, so the filtered extract is the
project's own snapshot. ([design](docs/design.md#data-sources), [data
sources](docs/research/data-sources.md#ais))

## Run it

Not run here: these commands come from the repo's docs and were not executed for this README.

Prerequisites: Docker with Compose, [uv](https://docs.astral.sh/uv/) and Python 3.12. No paid keys.
The database listens on host port 5434 as `postgresql://oil:oil@localhost:5434/oil`, and
`DATABASE_URL` overrides it. The `postgis/postgis:16-3.4` image is published for amd64 only, so on
an arm64 machine it runs emulated.

```sh
docker compose -f infra/docker-compose.yml up -d
cd pipeline
uv sync
uv run oil --help
uv run oil db migrate

# Checks. The tests need the database above and use the committed one-day fixture.
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest

# Full run order, against the running database.
uv run oil ais probe          # committed as config/ais_sources.yaml
uv run oil ais backfill --port corpus_christi --from 2023-01-01 --to 2025-09-30 --workers 2
uv run oil port seed          # committed as the version-0 features in the terminals geojson
uv run oil port load
uv run oil ais load --port corpus_christi --from 2023-01-01 --to 2025-09-30
uv run oil vessels build
uv run oil port derive --port corpus_christi
uv run oil events detect --port corpus_christi
uv run oil truth portwatch --port corpus_christi
uv run oil report m1 --port corpus_christi --check
```

`ais probe` and `port seed` write files that are committed, so a fresh clone can skip both. The
backfill is the long step. It pulls about 250 GB from NOAA for the 1,004-day span and takes about
five hours. It is resumable, so an interrupted run picks up where it stopped.

## Credits and licence

- AIS positions come from NOAA MarineCadastre, a free US government archive. The test fixture is a
  filtered extract of the 2024-03-05 daily file and includes vessel MMSIs as broadcast.
- Port-call counts come from IMF PortWatch at run time. No PortWatch data is committed.
- Census, EIA, Global Fishing Watch (non-commercial licence), Copernicus Sentinel imagery and
  aisstream.io appear in the design as planned inputs. None of them is used yet.
- Python dependencies are listed in `pipeline/pyproject.toml` and keep their own licences.

MIT licence, Copyright (c) 2026 Kevin Shibuya. See [LICENSE](LICENSE).
