# Contract: converting tanker draught into an estimated crude cargo per port visit

## Use
A design decision for a personal, zero-budget oil export nowcasting project. The output is a written
recommendation the project owner will lean on when the implementation plan is written. The owner is not
a naval architect and does not know the maths; the recommendation must be explainable in plain language.
Return your reasoning, not just a verdict.

## Setting (settled, do not reopen)
- Target: weekly seaborne crude exports from Corpus Christi, Texas (Ingleside terminals: Enbridge Ingleside
  Energy Center and South Texas Gateway), nowcast before official numbers, backtested over 2023-2025.
- Ground truth: monthly crude exports by port from the US Census port-level API (HS 2709, port 5311),
  monthly crude tonnage from the Port of Corpus Christi, and weekly national crude exports from EIA.
- Model: deterministic accounting baseline (each laden departure contributes an estimated cargo, summed
  per week), with a regression on top only if it beats the baseline.
- Export event: a crude tanker dwelling at a named crude berth polygon beyond a threshold, then leaving seaward.
- AIS source: NOAA MarineCadastre daily archive, 1-minute positions, US waters. Per position it carries:
  MMSI, timestamp, lat, lon, SOG, COG, heading, vessel name, IMO, call sign, VesselType code, Status,
  Length, Width, Draft (metres), Cargo code, TransceiverClass. No DWT, no tonnes-per-centimetre, no
  block coefficient. Draft is the crew-entered static "maximum present static draught"; it is often stale,
  sometimes never changed between laden and ballast legs, sometimes updated hours after departure.
- Vessel particulars beyond AIS: no paid registry. Free sources are patchy (Equasis gives gross tonnage
  after registration; IMO GISIS is limited). Assume DWT must usually be estimated from Length, Width and
  vessel class, with a small hand-curated table for the frequent callers (a few hundred tankers).
- Terminal specifics: Ingleside berths take VLCCs at partial loads (channel depth limits, roughly 1.5 million
  barrels for a VLCC), Suezmax and Aframax at full or near-full loads. Reverse lightering (a VLCC topping up
  offshore from smaller tankers) happens off the Texas coast; those STS events are outside the berth
  polygons and inside US waters.

## Question
Which method should the baseline use to turn a port visit into barrels of crude loaded, given the
unreliability of AIS draught? Candidates, not exhaustive:
(a) Interpolate linearly between a ballast draught and a scantling/summer draught for the vessel's class,
    scaled by DWT, using the departure draught only.
(b) Per-visit delta: departure draught minus arrival draught, times an estimated tonnes-per-centimetre
    (TPC) for the hull, converted to barrels with a crude density; falls back when draught does not change.
(c) Ignore draught entirely: DWT times a class-typical load factor observed at this port (e.g. VLCC partial
    loads), calibrated against the monthly truth.
(d) A hierarchy that picks per visit based on draught data quality, recording the method used.

## Deliverable
1. Recommended method with the exact formulas, every input named and where it comes from
   (AIS field, class table, calibration against truth).
2. How to estimate DWT and TPC from AIS Length and Width when the vessel is not in the curated table,
   with expected error.
3. The draught-quality rules: how to decide a draught reading is trustworthy (timing relative to
   departure, change vs arrival value, plausibility against class limits), and what to do with
   untrustworthy ones.
4. How the per-visit estimates get calibrated against monthly truth without leaking the test period.
5. Failure modes ranked by how much they would bias weekly totals, and which are detectable from data.
6. What to record per export event so the method is auditable and swappable later.
7. A plain-language explanation, five sentences at most, of draught, DWT, TPC, ballast and load factor,
   for the project owner.
Keep it under 1200 words. Where you are unsure of a physical constant or a typical class value, say so
and give a range rather than a single number.
