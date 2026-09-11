# Oil tracker

Crude oil export tracking for the Port of Corpus Christi, built from NOAA AIS.
Milestone 1 turns three years of AIS into a PostGIS store of tanker positions,
vessels, berth visits and export events, cross-checked against PortWatch port
calls.

The design is `docs/design.md`; the vocabulary is `docs/glossary.md`; the
milestone 1 plan is `docs/plans/2026-09-11-m1-ingest.md`.

## Starting PostGIS

```sh
docker compose -f infra/docker-compose.yml up -d
```

The database listens on host port **5434** (`postgresql://oil:oil@localhost:5434/oil`).
Override it with `DATABASE_URL`.

## Running the CLI

```sh
cd pipeline
uv sync
uv run oil --help
uv run oil db migrate
```

## Run order

From `pipeline/`, against a running database:

```sh
uv run oil db migrate
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

`ais probe` and `port seed` write files that are committed, so a fresh clone can
skip both: `config/ais_sources.yaml` already names the working archive per year,
and `config/ports/corpus_christi.terminals.geojson` already holds the version-0
seed circles. The backfill is the long step, about five hours for the 1,004-day
span; it is resumable, so an interrupted run picks up where it stopped.

## Checks

From `pipeline/`:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```
