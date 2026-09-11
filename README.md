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

## Checks

From `pipeline/`:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```
