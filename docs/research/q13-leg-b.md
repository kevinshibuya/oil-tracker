**Use (c) for the initial baseline: estimated DWT × a calibrated port/class load factor.** Preserve draught and method-selection fields so alternatives can be evaluated later. A hierarchy becomes useful when its branches have demonstrated predictive value; draught quality alone does not establish cargo accuracy.

The decisive problem with (b) is ballast discharge during loading. The approximate mass balance is:

\[
M_{\rm loaded}=100\,\overline{TPC}(T_d-T_a)+N_a-N_d
\]

Here \(T_a,T_d\) are **mean** arrival/departure draughts in metres, \(\overline{TPC}\) is average tonnes/cm across that interval, and \(N_a,N_d\) are non-cargo masses, including ballast and fuel. AIS supplies neither mean draught nor those masses. Consequently, even perfectly updated AIS draught cannot resolve the equation. [Draught-survey guidance](https://www.ukpandi.com/fileadmin/uploads/ukpandi/Documents/uk-p-i-club/carefully-to-carry/2023/UKPI_Carefully_to_Carry_2023_16.pdf)

Method (a) also assumes transferable ballast conditions and equates a full-load endpoint with cargo capacity. Departure cargo additionally includes anything already aboard on arrival.

For each qualifying visit \(i\), use:

\[
M_i=D_i f_{c(i)},\qquad
Q_i=\frac{M_i}{0.158987294928\,\rho_o},\qquad
Q_w=\sum_{i:\,\text{seaward departure in }w}Q_i
\]

\(D_i\): DWT in metric tonnes, curated or estimated below. \(c(i)\): vessel size class from curated particulars or AIS Length/Width templates. \(f_c\): expected **cargo added at these terminals**, divided by DWT, from the class table and subsequent calibration. \(\rho_o\): crude density in tonnes/m³ at the reporting reference temperature; use **0.78–0.85**, midpoint initially, as an explicit light-crude assumption. The denominator converts tonnes into petroleum barrels. [Barrel definition](https://www.eia.gov/tools/glossary/index.php?id=barrel)

These are starting assumptions, not measured port averages:

| Class | Initial \(f_c\) range | Rough summer-draught screening range |
|---|---:|---:|
| Aframax | 0.85–0.98 | 14–16 m |
| Suezmax | 0.85–0.98 | 16–19 m |
| VLCC | 0.60–0.70 | 20–23 m |

Use factor midpoints initially. The VLCC assumption yields approximately the stipulated 1.5 million barrels for a 300,000-DWT vessel. Tank capacity, consumables and previous cargo can lower actual intake.

**Estimate missing particulars empirically.** For valid, stable AIS length \(L\) and width \(B\), both metres:

\[
\widehat D=k_cLB^2,\qquad
k_c=\operatorname{median}_{j\in c}\frac{D_j}{L_jB_j^2}
\]

Reference vessels \(j\) come from the curated table, using public owner/shipyard particulars. This is a geometric approximation assuming similar proportions within each class. Before sufficient examples exist, **0.22–0.28 tonnes/m³** is a rough working range for \(k_c\); use its midpoint and label the estimate provisional. Gross tonnage is not DWT.

For diagnostic TPC:

\[
\widehat{TPC}=0.01\,\rho_w C_{WP}\alpha LB
\]

\(\rho_w\): water density, approximately **1.000–1.025 tonnes/m³**; \(\alpha\): waterline-length/overall-length approximation, **0.94–0.98**; \(C_{WP}\): waterplane fullness, provisionally **0.85–0.95** for these tankers. Use seawater density and coefficient midpoints initially. These uncertain geometry factors belong in the class table. TPC changes with draught; a single value is approximate. [Physical relationship](https://tc.canada.ca/en/marine-transportation/publications/tp-14609-study-guide-applicants-fourth-class-engineer-certificate-stcw-endorsement)

Budget **±15–25% DWT** and **±10–20% TPC** as unvalidated engineering allowances, with larger errors for unusual hulls or incorrect dimensions. Measure DWT error by leaving each curated vessel out in turn; empirical TPC validation requires actual hydrostatic particulars.

**Screen draught for plausibility, without treating screening as proof.** Use these provisional rules:

- Arrival: last stable plateau within 12 hours before berthing. Departure: last stable plateau in the final two berth hours or first six outbound hours, stopping before any possible STS encounter. Require at least three observations spanning 30 minutes within 0.2 m.
- Reject missing/zero and saturated 25.5-m values. Require compatibility with vessel-specific limits, or broad class limits; flag exceedances above summer draught plus 0.5 m. Historical channel limits are additional checks, not substitutes for vessel draught.
- Require a rise exceeding 0.3 m for a “changed” flag. Unchanged, declining, contradictory or repeatedly identical values across known loading visits are uninformative. Repetition does not prove freshness: AIS reports can repeat stale entries.

AIS encodes maximum static draught in 0.1-m increments. [USCG specification](https://www.navcen.uscg.gov/ais-class-a-static-voyage-message-5)

Accept delayed updates only if available by the prediction cutoff and before possible offshore transfer. Otherwise retain the class estimate, flag uncertainty, and never interpret unchanged draught as zero cargo. Even screened readings remain diagnostic initially.

**Calibrate in tonnes before converting to barrels.** For matched monthly coverage, fit three bounded factors:

\[
\hat f=\arg\min_{0\le f_c\le0.98}
\sum_{m\in\mathcal T}
\left(\frac{Y_m-\sum_{i\in m}D_if_{c(i)}}{Y_m}\right)^2
+\lambda\sum_c(f_c-f_c^0)^2
\]

\(Y_m\) is monthly export tonnage; \(\mathcal T\) contains released training months; \(f^0\) contains table midpoints; \(\lambda\), selected on development data, discourages unsupported departures from those assumptions.

Use Census `VES_WGT_MO` for HS 2709/port 5311, converting kilograms to tonnes. Check Port figures’ direction and units—short tons require ×0.90718474. [Census fields](https://api.census.gov/data/timeseries/intltrade/exports/porths/variables.html)

Port-wide truth may exceed the two terminals’ coverage. If unmatched, keep physical factors fixed and fit a separate aggregate expansion \(h=\sum Y_m/\sum M_i\); do not assign that expansion to individual cargoes. Coverage and physical load factors cannot both be identified from these totals alone.

Use 2023–2024 for development and 2025 as untouched testing. Early-2023 predictions require fixed priors or pre-2023 training. Replay publication delays and AIS availability; never rescale test-month weeks using that month’s eventual truth. EIA national exports provide context, not local weekly validation. Monthly fit cannot prove correct weekly allocation.

Expected failure ranking:

| Rank | Failure and bias | Detectability |
|---|---|---|
| 1 | Missing/duplicate visits, non-export departures, coverage mismatch: entire cargoes added/lost | Partly detectable from tracks and reconciliation |
| 2 | STS double counting or offshore-updated draught: upward bias | Encounters detectable; transferred quantity uncertain |
| 3 | Prior cargo, variable partial loads, aborted loading: class estimate often high | Sometimes visible; cargo amount usually unknown |
| 4 | Wrong DWT/class or changing caller mix: persistent bias either way | Dimension/identity checks help |
| 5 | Week-boundary timing: large adjacent-week errors | Track gaps and timestamps reveal uncertainty |
| 6 | Density and unit mistakes: systematic scaling error | Units detectable; cargo density usually unknown |

Count terminal-loaded parcels once; offshore transfers redistribute them without creating another terminal loading.

Record event/parcel IDs, IMO/MMSI, berth/polygon version, arrival/departure times, AIS provenance and availability cutoff, dimensions/class sources, draught plateaus/timestamps/flags, DWT/TPC assumptions, factor/density versions, calibration cutoff, method and fallback reason, tonnes/barrels, sensitivity range, STS links and revisions.

Draught is how deeply a ship sits in water. DWT is its carrying allowance for cargo plus fuel, water, ballast and supplies. TPC estimates the tonnes needed to sink it one centimetre deeper. Ballast is water carried for stability, often discharged while cargo loads. Load factor here means the estimated crude added during this visit as a fraction of DWT.