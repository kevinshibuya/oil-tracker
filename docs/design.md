# Oil Tracker: design summary

Grilled 2026-09-10 to 2026-09-11 on Fable 5.1. Every decision below was put to the owner and settled.
This file is the primary source for the implementation plans. Facts came from scouts with web search;
the Q13 method came from a two-leg reasoning round (Fable and gpt-6-astra) and was consolidated here.

## Goal

Nowcast weekly seaborne crude exports from Corpus Christi, Texas, before the official numbers publish,
from ship movements and satellite imagery, and show the evidence on a globe you can zoom into port
and click ship by ship.

## Non-goals

- Predicting production at wells. Ships observe exports and inventory change, not production.
- Forecasting next month. The horizon is the current week.
- Product tankers, refined fuels, LNG. Crude only.
- Paid data. Zero budget.
- Deployment. Local only until a later slice decides hosting.

## Settled decisions

### Target and truth
- Target: weekly barrels of crude loaded for export at the two Ingleside terminals, Enbridge Ingleside
  Energy Center and South Texas Gateway. Inner-harbour crude docks are out of v1 but supported by config.
- Nowcast, not forecast. Each week's estimate is fixed at a stated cutoff before truth publishes.
- Primary truth, from milestone 3 where calibration first needs it: monthly crude exports by port
  from the Census port-level API, HS 2709, port 5311, in kilograms, lag about five weeks. Milestone 1
  verifies against IMF PortWatch port-call counts only. Secondary: Port of Corpus Christi monthly tonnage by commodity
  (short tons, PDF). Context only: EIA weekly national crude exports, series WCREXUS2, lag five days.
- Backtest span: 2023 to 2025, extendable by config. Development on 2023 and 2024, 2025 untouched
  until the end. Rolling-origin scoring: month m is scored with parameters fitted on months before m.

### Data sources
- AIS at berth scale: NOAA MarineCadastre daily national archive, one-minute positions, US waters.
  Daily files are 0.2 to 0.35 GB compressed (corrected 2026-09-11); the downloader streams and
  filters to the port bounding box without keeping the national files. Parquet exists from 2024, CSV before. The archive is a
  rolling ten-year window, so the filtered extract is the project's own snapshot.
- Ocean legs beyond the US exclusive economic zone: Global Fishing Watch API, thinned tanker tracks
  since 2012, plus AIS-gap, encounter and port-visit events. Non-commercial licence.
- Port calls cross-check: IMF PortWatch daily port calls and tanker tonnage, 2019 onward, free.
- Optical: Sentinel-2, 10 m, about five-day revisit, via Copernicus Data Space (free account, quotas).
- SAR: Sentinel-1 GRD, about 10 m, two- to six-day revisit, same source.
- Vessel particulars: no registry. A small curated table of deadweight, tonnes per centimetre and
  scantling draught for the frequent callers; class-society public registers are the source. Every
  other vessel is estimated from AIS length and width.
- Live mode, last slice: aisstream.io websocket filtered to the port bounding box, archived
  continuously; weekly EIA pull; new Sentinel scenes as they land.

### Export event
A crude tanker (by AIS vessel type and size class) dwelling inside a named crude-berth polygon beyond a
dwell threshold, then leaving seaward. Terminal polygons are derived from data (amended 2026-09-11 at
plan review, since the executing agent has no GUI): the hull of low-speed tanker positions inside a
700 m seed circle around each published terminal centre, buffered, non-overlapping, then checked
against satellite imagery by the codex computer-use lane before the PR. Tank footprints are still
digitised once by hand, in milestone 4; OpenStreetMap coverage at Ingleside is uneven.

### Cargo per event (the Q13 method)
Two rungs, both recorded on every event, only one used for the total.
1. Load factor, the shipped baseline: estimated deadweight times a per-class factor, fitted against
   monthly Census tonnes on training months only. One factor per class (Aframax, Suezmax, VLCC), one
   global factor while a class has too few visits. Expected starting values: mid-size classes near
   full, VLCC about two thirds because of channel depth.
2. Deadweight scale, enabled only if it beats rung 1 out of sample: cargo tonnes equal deadweight
   minus (scantling draught minus departure draught, in centimetres) times tonnes per centimetre,
   minus consumables (one to one and a half percent of deadweight). Requires a trusted departure
   draught: risen at least 2 m from arrival (1.5 m for small classes), within class and channel limits,
   at least 55 percent of scantling, not a centimetre-exact copy of the previous departure, taken from
   a stable plateau within 24 hours after leaving the berth and before any offshore encounter.
   Untrusted draught falls back to rung 1 with a reason code.
- Rejected: arrival-to-departure draught delta, and linear interpolation from a ballast draught.
  Tankers discharge ballast while loading, so the hull barely sinks; both methods undercount by up
  to half on a VLCC.
- Deadweight and tonnes per centimetre when uncurated: from AIS length and width with class-typical
  coefficients, roughly plus or minus ten percent when the class is right. Class boxes by length and
  beam; anything outside every box is flagged unknown.
- Tonnes to barrels at the end only, 7.6 to 7.8 barrels per tonne for the light crude loaded here.
- Calibration in tonnes against Census. If the fitted scalar drifts above about 1.05, crude is
  loading outside the polygons; add berths rather than let the scalar hide it.
- Per-event record: vessel identity, class and its source, length, beam, deadweight, tonnes per
  centimetre, scantling draught with sources, berth id and polygon version, entry and exit times,
  arrival draught, every candidate departure draught with timestamp, chosen value and reason codes,
  method used, raw tonnes, factors with their fit window, density, barrels, next destination or
  ship-to-ship flag, code version.
- Reverse lightering off the Texas coast: count the terminal loading once; the offshore transfer
  redistributes it. Encounter events from Global Fishing Watch mark these.

### Model
Deterministic accounting baseline: sum of event cargo per week. A regression on weekly features
(laden departures, summed deadweight, PortWatch tonnage, tank fill change, AIS completeness) only if
it beats the baseline on held-out months. Everything the globe shows per ship comes from the baseline.

### Tank signal
Sentinel-2 at 10 m makes a floating-roof shadow sub-pixel, so the output is a fill class per tank
(three or four levels), not a volume. Aggregated per terminal, used as a model feature and shown on
the globe with the crop that produced it. Sentinel-1 coherent-scatterer methods are the published
alternative and share the SAR download.

### SAR signal
Corpus Christi has no shadow fleet. SAR's job here is an AIS-completeness check: detect vessels at
berth and anchorage on each Sentinel-1 pass (simple CFAR, no ML), match to AIS positions at the
acquisition instant, report unmatched detections. A logged metric and a globe layer, not a model
feature in v1. Configured so a second port is a config change.

### Globe and UI
- Stack: React, TypeScript, Vite, MapLibre GL JS with globe projection, deck.gl on top. Chosen because maritime analytics firms ship 2D Mapbox-API maps in React; CesiumJS reads as
  defence and digital twins. First globe slice includes a spike proving glTF ship models render
  over the MapLibre globe.
- Look: a mix. Realistic where the evidence is real: Sentinel-2 true-colour mosaic draped at
  terminal zoom, real dock and tank footprints extruded. Stylized elsewhere: the globe, the ships,
  the UI. Concept frames (globe, port zoom, ship detail, inspector) generated on gpt-image-2
  through the codex-image lane, then transcribed with the design-from-references skill before UI code.
- 3D models: at most four hulls (Aframax, Suezmax, VLCC, generic product tanker). Pipeline: concept
  frame, then Blender Python written by astra through codex-implementation, run headless, exported
  to glTF. The pipeline is saved in the repo as a script and a short doc so a new hull is one
  command. First hull is a spike; if it misses the bar, fall back to CC0 glTF assets recoloured.
- Time: a slider replaying any week of the backtest, ships moving along tracks, nowcast versus
  actual updating as you scrub.
- Tracks: observed AIS to the EEZ edge, then the Global Fishing Watch continuation, drawn
  distinguishably.
- Click targets and panels: ship (identity, class, deadweight and source, draught history, voyage,
  cargo estimate with method and reasons); terminal (nowcast versus actual, laden departures, berth
  occupancy, tank fill classes); tank (fill history with Sentinel-2 crop); SAR detection (matched or
  unmatched, with chip); week (scoreboard row).

### Backend
- Python pipeline (uv), PostGIS in Docker Compose, FastAPI serving the globe. FastAPI over NestJS
  because the project already has two languages and the geospatial queries share the pipeline's
  models.
- Local only. The dev server is already running; agents never start one.

### Explain-this-week (RAG, last slice)
An additive panel, off by default. When the nowcast misses, retrieve and cite from: EIA weekly report
narrative, Port of Corpus Christi notices (fog closures, hurricanes), National Weather Service marine
advisories, the pipeline's own event log and data-quality flags. Tool calls into the database for
figures. The language model never produces a number. Model and API choices at plan time via the
Claude API reference.

### Glossary
`docs/glossary.md`, seeded from this session, grown by the docs layer as plans introduce terms.

## Milestones, in order

1. Ingest: AIS extract for the port bounding box (2023 to 2025), PostGIS schema, terminal polygons,
   export-event detection, vessel table, PortWatch loader. Verification: event counts per month look like the port's
   published sailing counts.
2. Globe: MapLibre globe plus deck.gl, glTF spike, tracks with time slider, ship and terminal panels
   on ingest data. Headless browser smoke on every slice that renders.
3. Nowcast: Census and port-tonnage truth loaders, Q13 rungs, calibration, rolling-origin scoreboard;
   EIA context. Panels gain cargo estimates and the scoreboard.
4. Tanks: Sentinel-2 download, tank footprints, fill classes, globe layer and panel.
5. SAR: Sentinel-1 download, CFAR detections, AIS matching, completeness metric, globe layer.
6. Models and look: concept frames, design transcription, Blender pipeline, four hulls, UI pass.
7. Live: aisstream archive, weekly EIA and Census pulls, scene watcher, current-week nowcast.
8. Explain-this-week panel.
9. Deployment, hosting decided then.

Each milestone is one plan file with its own review wave. Milestone 2 may start before 1 is fully
scored, since it only needs tracks and events.

## Known risks
- Draught quality. Both rungs and every reason code exist because AIS draught is crew-typed.
- Truth mismatch. Census port 5311 covers more than the two Ingleside terminals; the scalar drift
  check catches it. Reverse lightering may book under a different clearance.
- Volume. About 250 GB of daily files stream through the filter for three years; the downloader
  must be resumable and run in the background.
- Sub-pixel tanks. The fill class may prove noisy at 10 m; the slice is scored against export
  drawdowns before it is trusted as a feature.
- deck.gl over the MapLibre globe. The scene-graph layer at globe projection is unproven; the spike
  in milestone 2 settles it before any UI is built on it.
- Licences: Global Fishing Watch is non-commercial; Copernicus quotas throttle rather than block.
