# Calibrating the wind and loss constants

Status of each constant the pricing depends on, the data used, what was fitted, and how
the result compares with observations. Prepared 27 September 2026 on the `calibration`
branch.

## Summary

| Constant | Before | Now | Data | Evidence |
| --- | --- | --- | --- | --- |
| Radius of maximum wind | 30 km, every storm | Per storm, from intensity and latitude near Florida | HURDAT2 RMW, 2021-2025 | sourced |
| Outer decay exponent | 0.5 | 0.30, fitted to station peak gusts | Florida ASOS peaks, 19 hurricanes | sourced, calibrated |
| Land exposure factor | none (marine profile) | 0.775 on the sustained wind | Florida ASOS peaks, 19 hurricanes | sourced, calibrated |
| Gust factor | 1.25, assumed | 1.314, measured | Florida ASOS 2-minute pairs, 19 hurricanes | sourced |
| Taper start / cutoff | 200 / 300 km | 259 / 444 km, record quantiles | HURDAT2 34 kt radii | sourced |
| Damage curves | assumed fixtures | FEMA Hazus building loss functions, one-story masonry | Hazus Technical Manual via SimCenter's library | sourced |

The wind step is `sourced` end to end and validated against station observations,
including a leave-one-storm-out check that the calibration carries over to storms it
was not fitted on. The damage curves are now published Hazus functions, so the run as a
whole is `sourced`. "Sourced" means each number has a documented origin and a
reproducible derivation; it does not mean validated against this portfolio's claims.
The validation section says how large the wind errors are, and they are not small.

Every fixture is produced by a script from committed data, and a test refits each one
and checks the committed values:

```bash
cd backend
python scripts/fit_storm_size.py        # app/fixtures/storm_size_model.json
python scripts/fit_gust_factor.py       # app/fixtures/gust_factor_model.json
python scripts/calibrate_wind_field.py  # app/fixtures/wind_calibration.json
python scripts/validate_wind_field.py   # app/fixtures/wind_validation.json
python scripts/build_damage_curves.py   # app/fixtures/damage_curves.json
python -m pytest tests -q
```

## The data

### HURDAT2 wind radii (bundled)

NOAA's best-track file ships inside the `hurricane_simulator` wheel. Beyond the centre
track the simulator uses, each fix from 2004 on records the extent of 34, 50 and 64 kt
winds in four quadrants, and each fix from 2021 on records the radius of maximum wind.
`scripts/fit_storm_size.py` reads these directly (the simulator's loader drops them).

### Florida hurricane ASOS gust data (`data/calibration/fl_hurricane_gust_data`)

One-minute ASOS observations from 46 Florida airport stations during 19 hurricanes:
Charley, Frances, Ivan and Jeanne in 2004, Dennis, Katrina, Rita and Wilma in 2005, and
Hermine 2016 to Milton 2024. Built from the NCEI archive via the Iowa Environmental
Mesonet and paired minute by minute with the NHC best track. The calibration-ready
table has 526,303 two-minute windows with a mean of at least 10 kt and clean QC flags:
334,762 from the 2016-2024 storms and 191,541 from 2004-2005. Fewer airports archived
one-minute data then, 12 in 2004 and 37 in 2005, against 39 to 46 per storm later.
`PROVENANCE.md` in that folder records what was received and what was left out (the
raw ASOS file and the full one-minute parquet, both re-buildable with `build.py`).

Checked on receipt: the gust-factor column recomputes exactly from gust over mean; no
window has a gust below its mean; storm and station counts match the README; the
strongest observation (Punta Gorda in Ian, 76 kt mean, 117 kt gust) is consistent with
NHC's report for that station.

## 1. Storm size: fitted from HURDAT2

Restricted to hurricane-strength fixes:

- **Radius of maximum wind.** `ln(rmw_km) = 3.515 - 0.0099 * max_wind_kt + 0.0357 * latitude`
  on 633 fixes from 39 storms, 2021-2025. R² 0.41; residual standard deviation 0.49 in
  log space, so an individual storm can sit a factor of 1.6 either side. Clamped to
  8-80 km.
- **Taper.** The largest-quadrant 34 kt radius of 2,568 hurricane fixes: median 259 km,
  90th percentile 444 km.
- **Decay exponent from the radii,** for reference: the exponent that reproduces each
  fix's 64 kt radius has a median of 0.49. This describes the sustained mean wind and is
  superseded for pricing by the station-calibrated value in section 3.

| Intensity, latitude | RMW |
| --- | ---: |
| 64 kt at 25 N | 44 km |
| 96 kt at 26 N | 33 km |
| 120 kt at 26 N | 26 km |
| 140 kt at 27 N | 22 km |

**Where on the track.** The size is evaluated once per storm at its most intense fix
inside a window around Florida (22.5-32.5 N, 89.5-78 W), or at its overall peak if the
track never enters it, and held constant through the event. Sizing from the peak near
Florida rather than a peak far out at sea scored slightly better against the stations
and matches what the pricing is for.

## 2. Gust factor: measured at Florida stations

Windows with a two-minute mean of 34 kt or more, rows repaired from a parser
misalignment excluded, split by upwind exposure over 10 km:

| Two-minute mean | Land fetch | Ocean fetch |
| --- | ---: | ---: |
| 34-50 kt | 1.314 (n 9,807) | 1.265 (n 4,137) |
| 50-64 kt | 1.294 (n 604) | 1.254 (n 170) |
| 64 kt or more | 1.375 (n 79) | 1.215 (n 13) |

The platform value is the land-fetch median, **1.314**, with a 10th-90th percentile
range of 1.20-1.47. It was 1.333 on the 2016-2024 storms alone; the 2004-2005 windows
gust slightly less, and the 50-64 kt band, which had 285 land windows, now has 604. The curves are labelled "open terrain", the ASOS siting standard.

Two caveats travel with the number. The observations are ratios to a two-minute mean;
the model's sustained wind follows the best-track one-minute convention. Within a
window the larger of its two one-minute means is at least the two-minute mean, so
G(3s, 2min) is an upper bound on G(3s, 1min), and the two are close in steady hurricane
wind; no conversion factor was applied, because none could be sourced from this
container. And the hurricane-force tail is still thin (79 land windows, up from 49), so
the value rests on the 34-64 kt bands.

## 3. Profile shape: calibrated to station peak gusts

Two things neither dataset measures directly were fitted jointly against the peak gust
each station recorded in each storm: the outer decay exponent of the profile, and an
open-terrain land exposure factor on the sustained wind (the profile is marine and the
wind model has no land weakening, while every station and every priced property is on
land).

Method: run each storm's real best track (synoptic fixes) through wind_field with the
storm-size model and a unit gust factor; scale by the measured gust factor and each
candidate land factor; compare with the station's observed peak. Pairs: station within
250 km of the track, observed peak at least 40 kt, record not broken off after strong
wind. That is 209 station-storm pairs from all 19 storms, with 9 truncated records
listed separately.

Objective: minimum mean absolute error among candidates whose median modelled/observed
ratio is within 5% of one overall and within 10% of one where the observed gust was
64 kt or more. The second condition exists because the damage curves only respond above
about 65 kt; the first fit, without it, chose a shape that matched the many moderate
observations by pricing the damaging winds 16% low.

| | Decay | Land factor | Median ratio | MAE | Within 75 km | Beyond 75 km | Observed 64 kt+ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Reference (radii decay, no land factor) | 0.5 | 1.00 | 1.00 | 15.9 kt | 1.29 | 0.92 | 0.95 |
| **Chosen** | **0.30** | **0.775** | 1.04 | 13.4 kt | 1.11 | 1.01 | 0.91 |

On the 11 storms of 2016-2024 alone the choice was decay 0.275 and land factor 0.75;
adding 2004-2005 moved each one grid step.

### Does it hold up on storms it did not see?

Fitting and judging on the same 209 pairs would let the constants absorb the quirks of
these 19 storms, so the script also refits nineteen times, each time without one storm,
and scores that storm with constants that never saw it. Pooled over the held-out
storms:

| | In sample | Out of sample | Uncalibrated reference |
| --- | ---: | ---: | ---: |
| Mean absolute error | 13.4 kt | 14.3 kt | 15.9 kt |
| Median ratio | 1.04 | 1.03 | 1.00 |
| Observed 64 kt or more, median ratio | 0.91 | 0.92 | 0.95 |

The constants chosen without each storm stay within decay 0.25-0.40 and land factor
0.725-0.85, around the in-sample 0.30 and 0.775; the upper ends come from holding out
Katrina (and Milton, for the land factor). So the calibration carries over: a storm the fit has not met is priced about
6% less accurately than one it has (8% on 11 storms), and still better than with the
original constants. The individual folds show where the remaining error lives: holding
out Michael, Dennis or Matthew leaves them modelled 35-55% high, holding out Irma leaves
it 26% low, and Ivan, with 2 pairs, 42% low. That is storm-to-storm variation in
size and structure, which no constant can absorb.

The decay exponent is much flatter than the radii-implied 0.49 because observed peak
gusts away from the centre include rainband and convective gusts that a mean profile
does not carry; since the curves consume peak gusts, the peak-gust shape is the one
the pricing needs. The land factor stands in for surface roughness. Both are effective
values, fitted jointly: only their combination is validated, and neither should be
quoted as a physical measurement on its own. Note that the land factor and the gust
factor multiply to 1.02 (0.775 x 1.314), so the calibrated peak gust at a land station is,
on average, about the marine profile's sustained wind. The full candidate grid is in the fixture.

## 4. Validation: what the calibrated step gets right and wrong

`scripts/validate_wind_field.py`, same 209 pairs, calibrated step as the platform runs it:

| Subset | Pairs | Bias | MAE | Median ratio | Within 15% |
| --- | ---: | ---: | ---: | ---: | ---: |
| All | 209 | +2.9 kt | 13.5 kt | 1.04 | 40% |
| Within 75 km of track | 64 | +9.2 kt | 14.6 kt | 1.11 | 44% |
| Beyond 75 km | 145 | +0.2 kt | 13.0 kt | 1.01 | 38% |
| Observed 64 kt or more | 48 | -4.6 kt | 14.2 kt | 0.91 | 44% |

Per storm the median ratio ranges from 0.41 (Ivan, 2 stations) and 0.81 (Nicole) to
1.56 (Michael, 4 stations). Ian, Dennis and Matthew run high (1.19, 1.35, 1.37); Irma
runs low (0.82), consistent with Irma being far larger than the size model gives a
155 kt storm. Of the new storms, Katrina (1.05), Rita (0.98) and Jeanne (0.96) sit
close; Wilma (1.17) and Frances (1.18) run high.

What this means for a priced result:

- **Near the track the model is still about 11% high** on average, and the scatter is
  wide: fewer than half of stations are within 15% of the model. A single-home
  loss should be read as a central estimate with a spread of that order, not a point
  value.
- **The strongest observed gusts are modelled about 9% low.** At the top of the damage
  curve that is a material fraction of the loss. Adding 2004-2005 took this subset from
  34 to 48 pairs but did not close the gap.
- **The eyewall records that broke off** (Punta Gorda in Ian, Fort Myers and RSW in
  Irma, Sarasota in Milton, and Naples and Dade-Collier in Wilma among the new ones)
  are lower bounds, and all of them show the model above the last observed value, as
  they should. Four other broken-off records sit below the model's value (Daytona Beach
  in Dennis and Frances, Opa-locka and Pompano Beach in Wilma), all 55 km or more from
  the track.
- **Storm-to-storm variation in size and asymmetry** is the largest remaining error
  source and cannot be removed by constants. A size per track point from the best-track
  radii, and a forward-motion asymmetry, are the next modelling steps.

## 5. Effect on the demo

Three catalog storms against the ten-home demo portfolio, baseline curves, 5%
deductible, original constants (30 km, decay 0.5, gust factor 1.25, no land factor)
versus the calibrated step:

| Storm | Top gust before | Top gust after | Portfolio payout before | after |
| --- | ---: | ---: | ---: | ---: |
| SYN0155 | 187 mph | 150 mph | $952,768 | $312,812 |
| SYN0697 | 150 mph | 121 mph | $110,334 | $12,854 |
| SYN0973 | 142 mph | 116 mph | $21,037 | $0 |

These figures are from the 11-storm fit. Refitting on 19 storms raises the top gusts by
about 2 mph (SYN0155 150 to 152 mph, SYN0697 121 to 123 mph, SYN0973 unchanged at
116 mph), and the example endpoint's SYN0155 portfolio payout rises 11%, from $541,046 to
$601,313: the lower gust factor is more than offset by the flatter decay and higher land
factor.

The drop is large and comes mostly from the land factor and gust factor together
replacing 1.25 with an effective 1.00 near the track, where the demo homes sit. That
is the direction the stations point: the original constants overstated near-track
gusts by about a quarter against every observed landfall in the set.

## 6. Extending the observations

The 2004-2005 airports are now in; the hurricane-force tail went from 34 to 48 pairs
and the model still runs 9% low there. Airports alone will not fill that tail, so the
denser networks below are the next step.

### 2004 and 2005 seasons: fetched 27 September 2026

NCEI's one-minute ASOS archive begins around 2000, so the two most active Florida
seasons on record are available: Charley, Frances, Ivan and Jeanne in 2004; Dennis,
Katrina, Rita and Wilma in 2005. They add the cases that stress the model most, a very
small Charley and a very large Wilma. `storms_2004_2005.csv` holds their Florida windows
(from the bundled HURDAT2) and `raw/hurdat2_fl_2004_2005.txt` their best-track rows;
`fetch_asos_1min.py` downloads the observations from the Iowa Environmental Mesonet
mirror into the raw file `build.py` reads.

The data were fetched from the Iowa Mesonet through the Claude desktop app's browser on
a local machine, since this branch's cloud environment could not reach the host, using
the same query as `fetch_asos_1min.py`. Only the 76 stations whose archives begin by
2005 were requested; the rest have nothing that early. The 2004-2005 storms were built
on their own and appended to the committed 2016-2024 tables with `merge_extension.py`,
which gives the same rows as a single 19-storm build because `build.py` works storm by
storm; the committed 2016-2024 rows are byte-identical after the merge. To reproduce:

```bash
cd data/calibration/fl_hurricane_gust_data
python fetch_asos_1min.py storms_2004_2005.csv
python build.py
python merge_extension.py
cd ../../../backend
python scripts/fit_gust_factor.py && python scripts/calibrate_wind_field.py && python scripts/validate_wind_field.py
python -m pytest tests -q
```

Checked on the first run: the fetched sections carry the columns `build.py` expects,
and no 2004-2005 record shows the parser misalignment the README describes for later
years (the repair found no runs). HURDAT2 has no radius of maximum wind for these
seasons except at landfall, so `r_over_rmw` is sparse for them; the calibration does
not use it.

### Denser networks: what they are and what is needed

Airports stop reporting in the eyewall, so airport data alone will never populate the
tail well. Two sources do:

- **Florida Coastal Monitoring Program (FCMP) towers.** The University of Florida
  deploys 10 m instrumented towers into landfalling hurricanes and has archives for
  most Florida landfalls since 1999, including Charley, Frances, Ivan, Jeanne, Dennis,
  Wilma, Irma, Michael and Ian. They record high-rate wind at known heights and
  exposures, exactly what the gust factor and the near-centre profile need. The data
  are per deployment rather than a single API; each tower record needs its position,
  height and exposure carried through into the pairs table with a `network` column so
  it can be weighted separately from ASOS. Host: `fcmp.ce.ufl.edu`.
- **NOAA/HRD H*Wind surface wind analyses.** Gridded analyses of the whole wind
  field, built from every observation platform in the storm, available from AOML for
  storms through 2013 (later analyses are commercial). For Charley, Wilma, Katrina and
  the other 2004-2005 storms they give the peak wind at every grid point, which
  validates the profile's shape everywhere rather than only where an airport happened
  to be. Host: `www.aoml.noaa.gov`.

Neither is a drop-in for the current pairs table. FCMP fits the existing scheme after
a per-tower conversion (their sustained winds are 1-minute at 10 m, so no averaging-
period caveat); H*Wind is a field rather than point observations and would be compared
with the model's field over a grid.

To run any of this from a cloud session, the environment's network policy needs these
hosts allowed: `fcmp.ce.ufl.edu` and `www.aoml.noaa.gov` for these two, and
`mesonet.agron.iastate.edu` or `www.ncei.noaa.gov` to refetch the airports.

## 7. Damage curves: FEMA Hazus

The curves were an assumed formula with upgrades as flat percentage cuts. They are now
built by `scripts/build_damage_curves.py` from the FEMA Hazus hurricane model's building
loss functions, taken from the machine-readable copy in NHERI SimCenter's Damage and
Loss Model Library (`data/calibration/hazus_hurricane_loss` has the extract and its
provenance). Hazus was chosen because it is published, validated by its authors against
insurance losses (Vickery et al. 2006), and indexed by exactly the features the
platform's upgrades change. Florida's own public model (FPHLM) would be the natural
alternative, but its damage ratios by wind speed are withheld as trade secret in its
public submission to the state commission.

**Wind basis.** Hazus takes the open-terrain peak gust at 10 m, which is what the wind
step produces, so no conversion is applied. The home's surroundings are a separate Hazus
input, set to suburban roughness (0.35 m). Setting it to open terrain as well would
count the land reduction twice: at 140 mph it would raise the pre-2002 loss from 39% to
74%.

**Mapping.** Every home is a one-story masonry single-family house with a wood-truss
roof (Hazus M.SF.1):

| Platform curve | Hazus features |
| --- | --- |
| pre-2002 baseline | roof-to-wall toe-nails, 6d deck nails, no secondary water resistance, no shutters |
| pre-2002 + shutters | the same, with shutters |
| pre-2002 + roof straps | straps instead of toe-nails |
| post-2002 baseline | straps, 8d deck nails, secondary water resistance, no shutters |
| post-2002 + shutters | the same, with shutters |

Each of the five is published three ways: the Hazus gable-roof curve, the Hazus hip-roof
curve, and their mean ("blended"), 15 curves in total. A hip roof loses about half as
much at 140 mph. A property that declares its own `roof_shape` gets the matching curve;
one that does not gets blended - see "Roof shape" below. Wood-frame curves differ from
masonry by at most 2 points of replacement cost for four of the five, and by up to 9 for
post-2002 with shutters; masonry reinforcing moves them by under one.

| Peak gust | Pre-2002, before → now | + shutters, before → now | Post-2002, before → now |
| --- | ---: | ---: | ---: |
| 105 mph | 2.7% → 2.1% | 2.0% → 1.9% | 1.6% → 1.7% |
| 120 mph | 6.5% → 7.2% | 4.9% → 4.8% | 3.9% → 3.4% |
| 140 mph | 14.5% → 38.8% | 10.9% → 18.7% | 8.7% → 13.2% |
| 160 mph | 26.1% → 81.8% | 19.6% → 51.1% | 15.7% → 39.2% |
| 180 mph | 41.5% → 97.4% | 31.1% → 82.9% | 24.9% → 73.7% |

Below about 120 mph little changes. Above it the old curves were far too flat, and the
upgrades were worth far less than Hazus gives them: at 140 mph shutters now cut an older
home's loss by 52% and roof straps by 34%, against the flat 25% and 20% assumed before.

**Effect on the demo** (ten-home demo portfolio, 19-storm wind calibration, per-home
baseline payouts summed, 5% deductible):

| Storm | Top gust | Baseline payout, before → now | Avoided by shutters, before → now |
| --- | ---: | ---: | ---: |
| SYN0155 | 152 mph | $348,688 → $1,203,610 | $121,204 → $691,687 |
| SYN0697 | 123 mph | $19,252 → $38,273 | $13,703 → $30,195 |
| SYN0973 | 116 mph | $0 → $0 | $0 → $0 |

**Adjustments to the source.** The engine refuses a curve whose damage falls as wind
rises, and an upgrade that adds damage would show as a negative avoided payout. Every
curve - gable, hip and blended alike, each on its own - is made nondecreasing (the
largest change is 2.5% of replacement cost: isolating gable and hip needs a bigger
correction than fixing the blend, because averaging two curves together had been
smoothing out some of each one's own noise). Within each roof shape, every upgrade is
capped at its baseline, the post-2002 baseline at the pre-2002 one (largest change 0.03%,
simulation noise below 105 mph). Both are recorded in the fixture.

### Roof shape: gable and hip curves, added 27 September 2026

The equal gable/hip mix was the single largest assumption in the curves themselves.
`scripts/build_damage_curves.py` now publishes the gable curve, the hip curve and their
blend for every class and upgrade, `app/claims.py` accepts a `roof_shape` ("gable" or
"hip") on each property, and `find_curve` picks the matching curve - falling back to
blended for "unknown" or any curve set that never split gable from hip, so declaring a
shape can never make a property worse off than not declaring one. The
`/api/v1/damage-curves` response and `metadata.curve_provenance` carry the split too.

This moves the assumption, it does not remove it. Two things are still unsourced:

- **Which shape an unlabeled home has.** No source for Florida's actual gable/hip mix
  exists, so "unknown" still falls back to the 50/50 blend - now a documented default
  rather than the only option, but still a guess.
- **Real per-home data.** The ten-property demo portfolio's `roof_shape` values (see
  `app/fixtures/example_portfolio.json`, `scripts/assign_demo_roof_shapes.py`) are a
  seeded random draw - 8 gable, 2 hip - not a classification of anything. They exist so
  this code path has a mix of both to run against. The demo portfolio's own coordinates
  are downtown/commercial buildings, not single-family homes, which is one reason a real
  classifier was not run against it; sourcing real roof shape for a real portfolio
  (aerial imagery and a shape classifier, or Florida's OIR-B1-1802 wind-mitigation
  inspection form) is separate work, not yet started.

**Effect of declaring a shape, one demo property.** P001 (Miami, $850,000 replacement
cost, pre-2002 baseline, no upgrade), SYN0155, 152.2 mph gust:

| roof_shape | Damage fraction | Baseline payout (5% deductible) |
| --- | ---: | ---: |
| Unlabeled (blended, what every property got before this change) | 66.3% | $521,386 |
| "gable" (P001's actual assignment) | 75.6% | $599,749 |
| "hip" (shown for contrast; not P001's assignment) | 57.1% | $443,012 |

The two demo properties assigned "hip" (P003 Tampa, P009 Naples) are not stressed hard
enough by any storm in the shipped catalog for the shape to change a nonzero payout -
their best-case peak gusts across the whole catalog are 83.5 mph and 98.0 mph, where
gable and hip fractions are both under 1.5% and well inside the deductible. The P001
comparison above, from the curves directly, is what a harder-hit hip-roofed home would
see.

**What is still open.**

- **Building detail per home.** Story count, wall type, deck nailing and secondary
  water resistance vary by home and year built; the platform has only the two classes.
  Hazus has curves for each, so more property attributes would feed straight in.
- **Scope.** Building only. Contents and loss of use are separate Hazus functions; the
  payouts cover the structure.
- **Low winds.** Hazus understates small losses below about 100 mph, where fallen trees
  do much of the damage.
- **Not checked against claims.** Hazus was validated on past storms by its authors,
  not on this portfolio. Insurer claims by wind speed and construction class would test
  both the curves and the mapping, and the Finance workbook's upgrade damage-effect
  request is still unanswered.
