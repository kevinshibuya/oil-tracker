# Q13: draught to barrels per port visit

Class values below are from memory of tanker particulars, not from the contract; treat each as ±10% until checked against two or three curated ships.

## Verdict

Use (d), with the primary rung being the **deadweight-scale** method, not (a) as written. Both (a) and (b) are structurally biased on the events carrying most barrels here, VLCC partial loads:

- (b) fails because a tanker pumps ballast out while it loads. Arrival draught is lightship plus ~100k t of ballast on a VLCC; departure is lightship plus cargo. Example: 10.0 m in, 15.5 m out, TPC 178 t/cm gives 5.5 × 100 × 178 = 97,900 t, about half the real cargo. Arrival draught is also the staler reading.
- (a) anchors zero cargo at the ballast draught, the same mistake in another form: (15.5 − 10)/(21 − 10) × 300,000 = 150,000 t, ~25% low. It is only right at full load, so it would look fine on Aframax/Suezmax and be wrong on VLCCs.

The deadweight scale anchors at the top (scantling draught = full DWT) and walks down with TPC. It is what a chief officer does with the ship's own tables and needs no ballast draught.

## 1. Method and formulas

Per laden departure:

```
short_t  = (T_scant − T_dep) × 100 × TPC       # tonnes below full deadweight
cargo_t  = DWT − short_t − consumables_t        # raw tonnes
bbl      = k_class × cargo_t × bbl_per_t
```

- `T_dep` (m): AIS Draft, selected by §3.
- `T_scant` (m): curated table, else class table: MR 11–13, Panamax/LR1 12–14, Aframax 14–15.5, Suezmax 16–17.5, VLCC 20–22.5.
- `DWT`, `TPC`: curated table, else §2.
- `consumables_t`: bunkers, water, stores, residual ballast; 1.0–1.5% of DWT. Constant, so calibration absorbs its error.
- `k_class`: §4 scalar, starts at 1.0.
- `bbl_per_t`: Corpus Christi crude is mostly WTI-Midland grade, API ~40–44, 7.6–7.8 bbl/t. Use 7.7; work in tonnes, convert at output.

Check: VLCC, DWT 300,000, T_scant 21.0, T_dep 15.5, TPC 178, consumables 3,000: 300,000 − 97,900 − 3,000 = 199,100 t × 7.7 = 1.53 mb, matching the contract's ~1.5 mb VLCC figure.

Fallback rung when §3 rejects the draught: `cargo_t = DWT × LF_class`, where `LF_class` is the median of `cargo_t / DWT` over trusted-draught visits of that class at this port in the training window, not a guessed number. Expect VLCC ~0.60–0.70, Suezmax/Aframax ~0.90–0.97; the VLCC value drifts up as the channel deepened 2023–2025, so use a trailing 12 months.

## 2. DWT and TPC from Length and Width

Class box by AIS Length (LOA) and Width: ≤200 m MR; 200–235 m, beam ≤33 m Panamax/LR1; 235–255 m, beam 40–46 m Aframax; 255–295 m, beam 46–52 m Suezmax; >300 m, beam 56–62 m VLCC. Outside every box: `class=unknown`, flagged.

```
DWT ≈ 0.70 × LOA × B × T_scant_class   (t)
TPC ≈ 0.0088 × LOA × B                 (t/cm)
```

Derivation: displacement = 1.025 × Cb × Lbp × B × T with Cb ~0.82, Lbp ~0.96 LOA, DWT ~87% of full displacement: 1.025 × 0.82 × 0.96 × 0.87 ≈ 0.70. TPC = waterplane × 1.025/100 with Cwp ~0.88: 0.88 × 0.96 × 1.025/100 ≈ 0.0087.

Sanity: 333 × 60 VLCC gives DWT 294,000 and TPC 176; 244 × 42 × 14.9 Aframax gives 106,800 and 90, both inside the real ranges I remember.

Expected error with the right class: DWT ±8–12%, TPC ±5–8%; wrong class ±25%. On a VLCC partial a 10% DWT error becomes a 15% cargo error, so curate the frequent VLCCs first. Class-society public registers (ABS Record, DNV Vessel Register, LR Class Direct) list DWT free; from use, not from the contract.

## 3. Draught-quality rules

Window: draught values broadcast from 6 h before berth-polygon exit to 24 h after, or until the vessel leaves US waters.

Accept `T_dep` if all hold:
1. Changed from arrival by ≥2.0 m (Aframax and up) or ≥1.5 m (smaller). Unchanged means never updated.
2. ≤ T_scant_class + 0.3 m and ≤ the channel limit on that date (14.3 m before the 54 ft deepening reached Ingleside, 16.5 m after; changeover date needs checking).
3. ≥ 0.55 × T_scant_class; below that the ship is not laden.
4. With several candidates, take the latest passing 1–3; record hours after berth exit. Values first seen >24 h out are `late`, accepted only while still in US waters.

Otherwise reject, use the fallback, record the reason code. Flag (not reject) a `T_dep` equal to the vessel's previous departure value to the centimetre: crews copy last voyage's figure.

A 10 cm error is 1,780 t on a VLCC, under 1%; AIS resolution is fine. A stale ballast value is not, and rule 1 catches it.

## 4. Calibration without leakage

Compare in tonnes (Census gives kg), by departure month, against port 5311. Fit only `k_class` (one global `k` below ~15 visits per class) and `LF_class`. Rolling origin: for test month m, fit on months ≤ m − 1 with ≥12 months of history, so 2023 is fit-only and out-of-sample error is reported for 2024–2025. Never fit on all 36 months and report 2025.

`k` silently absorbs density and consumables (fine, constant) and crude loaded at 5311 berths outside the Ingleside polygons (not fine). If `k` sits above ~1.05, add inner-harbour crude berths instead of letting the scalar hide them.

## 5. Failure modes, ranked by bias on weekly totals

1. Missed or doubled departures (polygon edge, AIS gap at berth, MMSI change, dwell threshold). Whole cargoes. Detectable: count laden departures against port vessel counts.
2. Stale draught passing rule 1 (old laden value). 10–30% per event. Partly detectable via the repeat-value flag.
3. Wrong class or DWT on uncurated vessels. 10–25%, concentrated in fallback events. Detectable: L×B outliers.
4. Reverse lightering: Aframax loads at Ingleside, transfers to a VLCC offshore. Counted once, but Census may book it under the VLCC's clearance, possibly another district. A truth-alignment error, not a method error. Detectable: next stop is an STS zone, not a foreign port.
5. Channel-deepening drift in `LF_VLCC`. Detectable: trend in trusted VLCC `T_dep`.
6. TPC linearisation (2–3% on VLCC partials), density mix (±3%), brackish bay water (1–2%): all constant, absorbed by `k`, invisible in AIS.

## 6. Per-event record

MMSI, IMO, name; class and how assigned; LOA, B; DWT, TPC, T_scant with source (`curated`/`estimated`); berth id, entry/exit timestamps; arrival draught and timestamp; every candidate departure draught with timestamp and hours-after-exit; chosen `T_dep`, reason codes; method (`dws`/`lf`); consumables; raw tonnes; `k` and `LF` with fit-window id; density; barrels; next destination or STS flag; code version. Raw tonnes and scalars stay separate so a later method reruns from the same rows.

## 7. Plain language

Draught is how deep the hull sits in the water; a heavier ship sits deeper. Deadweight (DWT) is the total weight a ship can carry when sunk to its deepest legal line, fuel included. TPC is the tonnes it takes to push the hull down one more centimetre, so the centimetres still showing above the deepest line, times TPC, is the cargo it left behind. Ballast is seawater an empty tanker carries to stay stable, pumped out while the oil comes in, which is why "departure minus arrival draught" undercounts. Load factor is the fraction of DWT actually filled: near 1 for Aframax and Suezmax here, about two thirds for a VLCC the channel will not let fill.
