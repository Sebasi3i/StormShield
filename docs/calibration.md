# Calibrating the wind and loss constants

Status of each constant the pricing depends on, the data used, what was fitted, and how
the result compares with observations. Prepared 27 September 2026 on the `calibration`
branch.

## Summary

| Constant | Before | Now | Data | Evidence |
| --- | --- | --- | --- | --- |
| Radius of maximum wind | 30 km, every storm | Per storm, from intensity and latitude near Florida | HURDAT2 RMW, 2021-2025 | sourced |
| Outer decay exponent | 0.5 | 0.275, fitted to station peak gusts | Florida ASOS peaks, 11 hurricanes (primary cohort) | sourced, calibrated |
| Land exposure factor | none (marine profile) | 0.75 on the sustained wind | Florida ASOS peaks, 11 hurricanes (primary cohort) | sourced, calibrated |
| Gust factor | 1.25, assumed | 1.333, measured | Florida ASOS 2-minute pairs, 11 hurricanes (primary cohort) | sourced |
| Taper start / cutoff | 200 / 300 km | 259 / 444 km, record quantiles | HURDAT2 34 kt radii | sourced |
| Damage curves | assumed fixtures | FEMA Hazus building loss functions, split by roof shape | Hazus Technical Manual via SimCenter's library | sourced |

**Storm cohorts.** The ASOS gust data spans three explicit cohorts, joined and excluded
strictly by `storm_id` (never by storm name, which is not guaranteed unique across
seasons): **`primary_2016_2024`** (11 storms, 2016-2024) is the production cohort - every
number in the table above, and every figure in sections 2-4 unless marked otherwise, is
this cohort's own fit. **`legacy_2004_2005`** (8 storms) is evaluated only: the
production bundle is scored against it frozen, never refit, and it is never blended into
production. **`combined_2004_2024`** (all 19 storms) is an explicit alternative and
sensitivity check, never a silent default - `scripts/compare_calibration_cohorts.py`
scores it, the primary fit, and a gust-factor-only diagnostic against the same demo
storms and portfolio, and writes `app/fixtures/calibration_sensitivity.json` (section 5).
Cohort membership comes from `data/calibration/fl_hurricane_gust_data/storms_2016_2024.csv`
and `storms_2004_2005.csv`; requesting an unlisted cohort id is an error, not a guess.

The wind step is `sourced` end to end and validated against station observations,
including an 11-fold leave-one-storm-out check on the production cohort and a frozen
evaluation of the production bundle against the eight storms it has never seen at all.
The damage curves are now published Hazus functions, so the run as a whole is `sourced`.
"Sourced" means each number has a documented origin and a reproducible derivation; it
does not mean validated against this portfolio's claims. The validation section says how
large the wind errors are, and they are not small.

Every fixture is produced by a script from committed data, and a test refits each one
and checks the committed values. `build_calibration.py` runs the primary-cohort fit, the
legacy frozen evaluation, the combined-cohort sensitivity comparison and the sample API
response in one dependency-correct pass, and promotes every fixture together or not at
all; the scripts it calls also run individually, with `--cohort` selecting
`primary_2016_2024` (default), `legacy_2004_2005` or `combined_2004_2024`:

```bash
cd backend
python scripts/fit_storm_size.py            # app/fixtures/storm_size_model.json
python scripts/fit_gust_factor.py           # app/fixtures/gust_factor_model.json (primary cohort)
python scripts/calibrate_wind_field.py      # app/fixtures/wind_calibration.json (primary cohort)
python scripts/validate_wind_field.py       # app/fixtures/wind_validation.json (fitted/holdout/legacy only - add
                                             #   --combined-calibration <path> for combined_sensitivity too)
python scripts/compare_calibration_cohorts.py  # app/fixtures/calibration_sensitivity.json
python scripts/build_damage_curves.py       # app/fixtures/damage_curves.json
python -m pytest tests -q

# or, the four wind-calibration fixtures above (not storm size or damage curves)
# together, dependency-checked and promoted atomically:
python scripts/build_calibration.py
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
misalignment excluded, split by upwind exposure over 10 km. Each cohort's gust factor
comes only from its own windows - the figures below are three independently
reproducible fits (`scripts/fit_gust_factor.py --cohort <id>`), not adjustments of one
another, and only the primary one prices anything.

**Primary cohort (2016-2024, 11 storms) - production**, 41 stations, 10,602 windows:

| Two-minute mean | Land fetch | Ocean fetch |
| --- | ---: | ---: |
| 34-50 kt | 1.333 (n 7,519) | 1.289 (n 2,716) |
| 50-64 kt | 1.360 (n 285) | 1.320 (n 33) |
| 64 kt or more | 1.424 (n 49) | - (n 0) |

The production value is the land-fetch median across all bands, **1.333** (n 7,853,
10th-90th percentile 1.23-1.50). The curves are labelled "open terrain", the ASOS
siting standard.

**Legacy cohort (2004-2005, 8 storms) - evaluated only, never refit into
production**: 28 stations, 4,208 windows, land-fetch median **1.25** (n 2,637, range
1.16-1.38) - distinctly lower than the primary cohort's, and thinner in the
hurricane-force band (30 land windows).

**Combined cohort (2004-2024, 19 storms) - explicit alternative, never a silent
default**: pooling both eras gives 43 stations, 14,810 windows, land-fetch median
**1.314** (n 10,490, range 1.20-1.47):

| Two-minute mean | Land fetch | Ocean fetch |
| --- | ---: | ---: |
| 34-50 kt | 1.314 (n 9,807) | 1.265 (n 4,137) |
| 50-64 kt | 1.294 (n 604) | 1.254 (n 170) |
| 64 kt or more | 1.375 (n 79) | 1.215 (n 13) |

Two caveats travel with every one of these numbers. The observations are ratios to a
two-minute mean; the model's sustained wind follows the best-track one-minute
convention. Within a window the larger of its two one-minute means is at least the
two-minute mean, so G(3s, 2min) is an upper bound on G(3s, 1min), and the two are close
in steady hurricane wind; no conversion factor was applied, because none could be
sourced from this container. And the hurricane-force tail is thin in every cohort - 49
land windows for primary, 30 for legacy, 79 pooled - so each gust factor above rests
mainly on its 34-64 kt bands.

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
wind. On the primary cohort that is **156 station-storm pairs from 11 storms**, with 4
truncated records listed separately; the combined cohort (below) has 209 pairs from all
19, with 9 truncated.

Objective: minimum mean absolute error among candidates whose median modelled/observed
ratio is within 5% of one overall and within 10% of one where the observed gust was
64 kt or more. The second condition exists because the damage curves only respond above
about 65 kt; the first fit, without it, chose a shape that matched the many moderate
observations by pricing the damaging winds 16% low.

**Primary cohort (2016-2024, 11 storms) - production**, fit and scored on its own 156
pairs:

| | Decay | Land factor | Median ratio | MAE | Within 75 km | Beyond 75 km | Observed 64 kt+ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Reference (radii decay, no land factor, this cohort's own gust factor) | 0.5 | 1.00 | 0.98 | 16.6 kt | 1.23 | 0.90 | 0.94 |
| **Chosen (production)** | **0.275** | **0.75** | 1.03 | 14.1 kt | 1.13 | 0.99 | 0.90 |

**Combined cohort (2004-2024, 19 storms) - explicit alternative**, fit and scored
independently on its own 209 pairs, at its own gust factor (1.314, section 2), never the
primary cohort's:

| | Decay | Land factor | Median ratio | MAE | Within 75 km | Beyond 75 km | Observed 64 kt+ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **Chosen (alternative)** | **0.25** | **0.725** | 1.05 | 13.4 kt | 1.06 | 1.04 | 0.90 |

Adding the 2004-2005 storms moves the profile one grid step flatter on decay and one
step lower on land factor; the legacy cohort's own 8 storms are never fit for a profile
of their own (too few and too different in era to trust as a third production
candidate) - they only ever evaluate the frozen primary bundle, below.

### Does it hold up on storms it did not see?

Fitting and judging on the same 156 pairs would let the constants absorb the quirks of
these 11 storms, so `calibrate_wind_field.py` also refits eleven times on the primary
cohort, each time leaving one storm's pairs out of BOTH the gust-factor and the
profile fit, and scores that storm with constants that never saw it - never the one
global gust factor reused across every fold. Pooled over the 11 held-out storms:

| | In sample | Out of sample | Uncalibrated reference |
| --- | ---: | ---: | ---: |
| Mean absolute error | 14.1 kt | 15.7 kt | 16.6 kt |
| Median ratio | 1.03 | 0.99 | 0.98 |

The constants chosen without each storm stay within gust factor 1.324-1.359, decay
0.25-0.45 and land factor 0.70-0.975, around the in-sample 1.333, 0.275 and 0.75. So the
calibration carries over: a storm the fit has not met is priced about 11% less
accurately by pooled MAE than one it has, and still better than the uncalibrated
reference. Two of the eleven folds are thin enough to swing on their own: Idalia (1
pair) and Michael (4 pairs) are the storms with the fewest pairs in the cohort, and
holding either one out leaves it modelled roughly 50-66% high, which is why the mean of
the eleven folds' own MAE (17.0 kt) runs above the pooled-pairs figure - a handful of
sparse, extreme folds pull a per-storm average up more than they move the pooled total.
Among the better-sampled folds, Matthew, Sally and Ian run 30-39% high and Irma and
Nicole run 18-26% low; that is storm-to-storm variation in size and structure, which no
constant can absorb.

The decay exponent is much flatter than the radii-implied 0.49 because observed peak
gusts away from the centre include rainband and convective gusts that a mean profile
does not carry; since the curves consume peak gusts, the peak-gust shape is the one
the pricing needs. The land factor stands in for surface roughness. Both are effective
values, fitted jointly with the gust factor: only their combination is validated, and
neither should be quoted as a physical measurement on its own. Note that the land
factor and the gust factor multiply to 1.00 (0.75 x 1.333) for the production bundle, so
the calibrated peak gust at a land station is, on average, about the marine profile's
sustained wind; for the combined alternative they multiply to 0.95 (0.725 x 1.314), a
step below it. The full candidate grid for each cohort is in its own fixture
(`wind_calibration.json` for primary; the combined grid is recomputed by
`compare_calibration_cohorts.py` and is not separately persisted).

## 4. Validation: what the calibrated step gets right and wrong

`scripts/validate_wind_field.py` runs three separate checks, all with the same
truncated-record handling and column definitions, and none of them mixed together.

**Fitted (in-sample): the production bundle on the 156 pairs it was fitted from.**

| Subset | Pairs | Bias | MAE | Median ratio | Within 15% |
| --- | ---: | ---: | ---: | ---: | ---: |
| All | 156 | +3.5 kt | 14.1 kt | 1.03 | 37% |
| Within 75 km of track | 44 | +10.7 kt | 15.4 kt | 1.13 | 41% |
| Beyond 75 km | 112 | +0.6 kt | 13.6 kt | 0.99 | 35% |
| Observed 64 kt or more | 34 | -4.2 kt | 16.1 kt | 0.90 | 35% |

Per storm the median ratio ranges from 0.81 (Nicole, 16 stations) to 1.56 (Michael, 4
stations). Matthew, Sally and Ian run high (1.31-1.39); Irma runs low (0.83), consistent
with Irma being far larger than the size model gives a 155 kt storm.

**Frozen evaluation: the same production bundle, unchanged, scored on the 8 legacy
(2004-2005) storms it has never been fitted on at all.**

| Subset | Pairs | Bias | MAE | Median ratio | Within 15% |
| --- | ---: | ---: | ---: | ---: | ---: |
| All | 53 | +5.6 kt | 12.4 kt | 1.09 | 43% |
| Within 75 km of track | 20 | +5.9 kt | 12.6 kt | 1.09 | 45% |
| Beyond 75 km | 33 | +5.4 kt | 12.3 kt | 1.11 | 42% |
| Observed 64 kt or more | 14 | -2.5 kt | 8.0 kt | 0.94 | 71% |

This is a harder test than the leave-one-storm-out check in section 3: a different era,
with different station coverage, evaluated with the bundle exactly as shipped. The
result is, if anything, better than on the storms it was fitted on - most notably at
64 kt or more, where 71% of these legacy pairs land within 15% against 35% for the
production cohort's own strong-gust subset. With only 14 pairs that gap is not proof the
model performs better on unseen storms, but it is real evidence the calibration is not
merely overfit to its own 11 storms. Per storm the median ratio ranges from 0.41 (Ivan,
2 stations - a 2-pair fold is noise, not a finding) to 1.40 (Dennis, 7 stations); Katrina
(1.08), Rita (1.03) and Jeanne (0.99) sit close; Wilma (1.17), Frances (1.16) and Charley
(1.13) run high.

**Combined sensitivity: the alternative bundle, fit and scored on all 19 storms
together (never a candidate for production).**

| Subset | Pairs | Bias | MAE | Median ratio | Within 15% |
| --- | ---: | ---: | ---: | ---: | ---: |
| All | 209 | +3.4 kt | 13.4 kt | 1.05 | 39% |
| Within 75 km of track | 64 | +7.3 kt | 13.8 kt | 1.06 | 45% |
| Beyond 75 km | 145 | +1.7 kt | 13.2 kt | 1.04 | 36% |
| Observed 64 kt or more | 48 | -4.7 kt | 13.6 kt | 0.90 | 48% |

What this means for a priced result:

- **Near the track the production model is about 13% high** on average, and the scatter
  is wide: fewer than half of stations are within 15% of the model. A single-home loss
  should be read as a central estimate with a spread of that order, not a point value.
- **The strongest observed gusts are modelled about 10% low** in the production
  cohort's own fit (34 pairs) - though not in the frozen legacy evaluation (14 pairs,
  6% low) or the combined alternative (48 pairs, 10% low). At the top of the damage
  curve that is a material fraction of the loss.
- **The eyewall records that broke off** in the production cohort's own storms (Punta
  Gorda in Ian, Fort Myers and RSW in Irma, Sarasota in Milton) are lower bounds, and
  all of them show the model above the last observed value, as they should. Among the
  legacy storms, Daytona Beach in Dennis and Frances sits below the model's value, as do
  Opa-locka and Pompano Beach in Wilma, all 55 km or more from the track; Naples and West
  Palm Beach in Wilma sit above it, as expected for a lower bound (West Palm Beach, station
  DJT, was previously mislabeled here as "Dade-Collier").
- **Storm-to-storm variation in size and asymmetry** is the largest remaining error
  source and cannot be removed by constants. A size per track point from the best-track
  radii, and a forward-motion asymmetry, are the next modelling steps.

## 5. Effect on the demo

`scripts/compare_calibration_cohorts.py` prices the same three catalog storms against
the ten-home demo portfolio under every wind scenario with the demo's roof-shape
assignments, the Hazus curve set and the illustrative policy template all held
identical (`app/fixtures/calibration_sensitivity.json`'s `config_held_constant`), so a
payout difference below is attributable to the wind bundle alone. Baseline (no
upgrade) payouts, 5% deductible, summed over the portfolio:

| Storm | Top gust: before rework | now (primary, production) | combined (alternative) | Payout: before rework | now (primary) | combined |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SYN0155 | 152.4 mph | 149.7 mph | 142.6 mph | $1,360,844 | $1,221,228 | $912,888 |
| SYN0697 | 123.1 mph | 120.8 mph | 115.2 mph | $64,063 | $39,622 | $3,378 |
| SYN0973 | 116.2 mph | 115.6 mph | 111.5 mph | $0 | $0 | $0 |

"Before rework" is the production bundle this branch carried before the cohort split
(gust factor 1.314, decay 0.30, land factor 0.775 - what this document used to call "the
platform value"); "now" is the primary cohort's own fit, promoted to production by this
rework (gust factor 1.333, decay 0.275, land factor 0.75, section 3); "combined" is the
19-storm alternative (gust factor 1.314, decay 0.25, land factor 0.725), never promoted,
shown for sensitivity only. Moving to the primary cohort trims SYN0155's portfolio
payout by 10%, mostly from the lower land factor (0.75, down from 0.775) pulling down
near-track gusts a little more than the higher gust factor (1.333, up from 1.314) pulls
them up - the decay exponent barely matters this close to the track, where r is close
to the radius of maximum wind regardless of the exponent; moving further to the
combined alternative would cut it by another 25% on top of that, and drops SYN0697 from
a $39,622 event to essentially nothing ($3,378) - illustrating how much a demo-scale
comparison can swing on a cohort choice with only a handful of eligible storms, not a
claim that one wind bundle is more defensible than another beyond section 3-4's
evidence. Avoided-payout figures by upgrade, per-property detail, and the diagnostic
gust-factor-only scenario are all in the fixture; none of the three catalog storms
change SYN0973 from a below-deductible non-event.

## 6. Extending the observations

The 2004-2005 airports are in as the `legacy_2004_2005` cohort. Pooling them into the
combined cohort takes the hurricane-force tail from 34 pairs (the primary cohort alone)
to 48, and that pooled alternative still runs about 10% low there (section 4) - though
the frozen primary bundle, evaluated on the legacy storms by themselves, actually runs
closer (6% low on 14 pairs, section 4), which is encouraging but too thin a sample to
lean on. Either way, airports alone will not fill that tail, so the denser networks
below are the next step.

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
# fit_gust_factor.py and calibrate_wind_field.py default to the primary cohort, which
# does not include these storms; check them in explicitly, and via build_calibration.py
# for the dependency-correct combined/legacy artifacts:
python scripts/fit_gust_factor.py --cohort legacy_2004_2005
python scripts/fit_gust_factor.py --cohort combined_2004_2024
python scripts/build_calibration.py
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

**Effect on the demo** (ten-home demo portfolio, per-home baseline payouts summed, 5%
deductible; top gust and "before" figures use the wind bundle production carried at the
time - the combined 19-storm fit, gust factor 1.314, decay 0.30, land factor 0.775 -
which the cohort rework in sections 2-5 has since replaced with the primary cohort's own
fit; the "before"/"now" split here is old-curves-vs-Hazus-curves only, holding wind
constant, so it does not restate section 5's separate before/after on the wind bundle
itself):

| Storm | Top gust | Baseline payout, before → now | Avoided by shutters, before → now |
| --- | ---: | ---: | ---: |
| SYN0155 | 152 mph | $348,688 → $1,203,610 | $121,204 → $691,687 |
| SYN0697 | 123 mph | $19,252 → $38,273 | $13,703 → $30,195 |
| SYN0973 | 116 mph | $0 → $0 | $0 → $0 |

**Adjustments to the source.** The engine refuses a curve whose damage falls as wind
rises, and an upgrade that adds damage would show as a negative avoided payout. Every
curve - gable, hip and blended alike, each on its own - is made nondecreasing. The
largest such adjustment is 2.54 percentage points of replacement cost, on
`post_fbc_2002.shutters.gable` at 205 mph: isolating gable and hip needs a bigger
correction than fixing the blend, because averaging two curves together had been
smoothing out some of each one's own noise. Within each roof shape, every upgrade is
capped at its baseline, the post-2002 baseline at the pre-2002 one; the largest such cap
is 0.03 points, on `pre_fbc_2002.roof_straps.hip` at 100 mph, where the source curves
cross by simulation noise. Neither is only an aggregate maximum: every one of the 15
curves' own adjustment, and the wind speed it happens at, is recorded in the fixture's
`provenance.monotone_adjustment_detail` and `provenance.ordering_adjustment_detail`
(`app/fixtures/damage_curves.json`), largest first.

### Roof shape: gable and hip curves, added 27 September 2026

The equal gable/hip mix was the single largest assumption in the curves themselves.
`scripts/build_damage_curves.py` now publishes the gable curve, the hip curve and their
blend for every class and upgrade (`curve_set_id` `hazus-msf1-suburban-v2`, up from 5
curves to 15 - see `provenance.version_note` in the fixture), `app/claims.py` accepts a
`roof_shape` ("gable" or "hip") on each property, and curve selection falls back to
blended for "unknown"/empty or any curve set that never split gable from hip. That
fallback is a guarantee about lookup - a curve is always found - not about outcome:
declaring a shape can raise or lower a property's modeled damage relative to blended (see
the P001 comparison below, where declaring "gable" raises it). The
`/api/v1/damage-curves` response and `metadata.curve_provenance` carry the split too.

**Auditable selection.** Every storm-losses result now includes a `roof_shape_selection`
entry per property: its declared `roof_shape`, the shape that was actually resolved, the
exact `baseline_curve_id` and `upgrade_curve_ids` used, and (when a fallback happened)
why - a caller-input problem, such as a typo, or simply a curve set with no
shape-specific curve for that class/upgrade. Only the first kind also raises a
property-specific warning (`result.warnings`), naming the offending value, since a typo
is worth surfacing and an intentionally unspecified shape is not.

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
