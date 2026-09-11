# Data sources: facts verified 2026-09-10/11

Scout findings from web search. Each entry names what was verified and the caveat that matters.
Re-verify a URL pattern before a plan hard-codes it; providers move files.

## AIS

### NOAA MarineCadastre (US waters, historical, free, no key)
- https://marinecadastre.gov/ais/ and https://coast.noaa.gov/htdata/CMSP/AISDataHandler/
- 2015 onward: one national file per day, 1-minute filtered positions.
- Correction 2026-09-11 (checked by HTTP): 2024 and 2025 daily files live at
  `https://noaaocm.blob.core.windows.net/ais/csv2/csv{YYYY}/ais-{YYYY}-{MM}-{DD}.csv.zst`
  (Zstd CSV). 2023 and 2024 are also at
  `https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{YYYY}/AIS_{YYYY}_{MM}_{DD}.zip`; that host
  has nothing for 2025. GeoParquet for 2024 and 2025 is advertised but its index was empty.
- Size, measured: 0.2 to 0.35 GB compressed per day (2023-01-01 zip 319 MB; 2024-01-01 zst
  196 MB; 2025-09-30 zst 272 MB). The earlier "3.1 GB average" figure was wrong. 1004 days from
  2023-01-01 to 2025-09-30 is about 250 GB.
- Latest available when checked: 2025-10-01 exists. Rolling roughly ten-year window;
  the oldest year drops as a new one lands, so the project keeps its own filtered extract.
- The 2024 and 2025 CSV headers are unverified and NOAA notes a naming change from 2025; the
  plan's probe task records the real header per year.
- Columns (CSV): MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, IMO, CallSign,
  VesselType, Status, Length, Width, Draft, Cargo, TransceiverClass. Draft in metres; VesselType
  is the AIS type code (tankers 80 to 89).
- AccessAIS web tool (https://marinecadastre.gov/accessais/) exports by bounding box and date
  range but caps each order near 2 GB compressed, so it does not replace bulk daily files for a
  three-year span.
- Summary docs: https://coast.noaa.gov/data/marinecadastre/ais/point-data-summary.pdf and
  https://coast.noaa.gov/data/marinecadastre/ais/faq.pdf

### aisstream.io (global, live only, free)
- https://aisstream.io/documentation. WebSocket, API key, 3 connections per account, bounding-box
  and MMSI filters, bandwidth caps from September 2026, no SLA, no replay. Carries message 5 static
  data: maximum static draught, type, destination, ETA. Terrestrial coverage only.

### Global Fishing Watch API v3 (global, historical, free token, non-commercial)
- https://globalfishingwatch.org/our-apis/documentation/
- Vessel tracks endpoint `/vessels/{vesselId}/tracks`, thinned not raw; 4Wings gridded presence
  at 0.1 or 0.01 degree, hourly and coarser. History from 2012.
- Events API: encounters, loitering, port visits, AIS-off gaps, for carriers and tankers as well
  as fishing vessels. Encounter detection tuned to transshipment: two vessels under 500 m for
  two hours or more. Caveats page:
  https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/data-caveats

### AISHub, Danish Maritime Authority
- AISHub is free only if you feed a receiver. DMA is Danish waters only. Neither is used.

## Port calls

### IMF PortWatch (global, daily, free, open)
- https://portwatch.imf.org/pages/data-and-methodology
- Daily port calls and estimated tonnage split by cargo type (tanker, dry bulk, container) for
  2,065 ports and 28 chokepoints, 2019 onward, refreshed Tuesdays 09:00 ET. ArcGIS REST, GeoJSON,
  CSV. Counts per port and day, not per vessel. Corpus Christi is one of the ports; confirm its
  port id in the dataset before use.

### Kpler, Vortexa, UNCTAD
- No free API tier at Kpler or Vortexa. UNCTAD publishes quarterly aggregate port-call
  statistics only. None used.

## Ground truth

### Census USA Trade Online, port-level exports (monthly, free, API key)
- API: https://api.census.gov/data/timeseries/intltrade/exports/porths
- Variables: https://api.census.gov/data/timeseries/intltrade/exports/porths/variables.html
- Filter `E_COMMODITY=2709` (crude petroleum, HS 4-digit rolls up) and `PORT=5311`
  (Corpus Christi; Customs district 5300). Monthly from 2003. Vessel weight field `VES_WGT_MO`
  in kilograms; value fields also available. Lag about five weeks (FT-900 release in the first
  week of month M+2).

### Port of Corpus Christi Authority (monthly, free, PDF)
- Monthly Cargo Tonnage by Commodity report with a Crude Oil row split inbound and outbound, in
  short tons (multiply by 0.90718474 for tonnes). Posted under
  https://portofcc.com/wp-content/uploads/ with a versioned filename, e.g.
  Cargo_Tonnage_Report_by_Commodity322.pdf. Lag three to six weeks. No terminal breakdown.
- Quarterly and annual press releases, e.g.
  https://portofcc.com/port-of-corpus-christi-announces-fourth-quarter-2025-volumes-and-annual-tonnage/

### EIA (weekly, national, free, API key)
- https://www.eia.gov/opendata/ API v2. Weekly supply at `/v2/petroleum/sum/sndw/data`,
  crude exports series `WCREXUS2`. Published Wednesdays about 10:30 ET for the week ending the
  prior Friday. Customs-derived estimates, revised in the monthly series.

## Satellite

### Copernicus Data Space Ecosystem (free account)
- Quotas: https://documentation.dataspace.copernicus.eu/Quotas.html. Free tier: 12 TB per 30 days
  via OData or S3, 4 concurrent connections, 20 MB/s, 10,000 Sentinel Hub processing units and
  50,000 requests per month. Throttles rather than blocks.
- Sentinel-1 IW GRD about 10 m, revisit two to six days with S1A and S1C. Sentinel-2 10 m,
  about five-day revisit. Access via STAC, OData, S3, openEO or Sentinel Hub.

### Tank fill from shadows
- Zhu et al. 2019, Remote Sensing 11(7):793, https://doi.org/10.3390/rs11070793: sub-pixel
  shadow-length medians on sub-metre imagery give tank height MAE 0.238 m. At Sentinel-2 10 m a
  60 to 80 m tank is 6 to 8 pixels and the shadow is sub-pixel, so expect fill classes, not
  volumes. Sentinel-1 coherent-scatterer methods are the published alternative.
- OpenStreetMap tags `man_made=storage_tank` with `content=oil` or `fuel`, and
  `industrial=petroleum_terminal`; coverage around the Corpus Christi terminals is uneven.

## Terminals (coordinates approximate, plus or minus 0.01 degree)
- Enbridge Ingleside Energy Center (ex Moda, ex Occidental): 27.82 N, 97.20 W. Largest US crude
  export terminal by volume, four deepwater berths, VLCC partial loads about 1.5 million barrels,
  floating-roof tank farm about 15.6 million barrels.
- South Texas Gateway (Gibson Energy; built by Buckeye and Phillips 66): 27.83 N, 97.21 W.
  About 8.6 million barrels of floating-roof storage, two deepwater berths.
- Flint Hills Resources Ingleside: 27.83 N, 97.22 W.
- Inner harbour, out of v1: Buckeye Texas Partners 27.81 N, 97.42 W; NuStar North Beach 27.82 N,
  97.40 W; Citgo and Flint Hills refinery docks 27.81 N, 97.44 W; Epic Midstream and Harvest docks.
- Corpus Christi handles about half of US crude exports; capacity about 2.6 million barrels per
  day, pipeline-constrained. Sources: https://www.enbridge.com/about-us/liquids-pipelines/export-terminals
  and https://rbnenergy.com/daily-posts/blog/first-tanker-set-arrive-south-texas-gateway-crude-export-terminal

## Front-end libraries
- MapLibre GL JS 5 globe projection is a supported production feature:
  https://maplibre.org/roadmap/maplibre-gl-js/globe-view/
- deck.gl GlobeView is still experimental (https://github.com/visgl/deck.gl/issues/9199); deck.gl
  over MapLibre syncs its camera to MapLibre's globe:
  https://deck.gl/docs/developer-guide/base-maps/using-with-maplibre
- CesiumJS: Apache 2.0, Bentley-owned since September 2024, unchanged licence. Not used.
- Market evidence: no customer-facing maritime analytics product uses a 3D globe; Kpler's
  front-end posting names Leaflet or Mapbox GL; Spire dev-rel samples use Mapbox GL JS.
