# Calibrating the wind and loss constants

Status of each constant the pricing depends on, the data used, what was fitted, and how
the result compares with observations. Prepared 27 September 2026 on the `calibration`
branch.

## Summary

| Constant | Before | Now | Data | Evidence |
| --- | --- | --- | --- | --- |
| Radius of maximum wind | 30 km, every storm | Per storm, from intensity and latitude near Florida | HURDAT2 RMW, 2021-2025 | sourced |
| Outer decay exponent | 0.5 | 0.275, fitted to station peak gusts | Florida ASOS peaks, 11 hurricanes | sourced, calibrated |
| Land exposure factor | none (marine profile) | 0.75 on the sustained wind | Florida ASOS peaks, 11 hurricanes | sourced, calibrated |
| Gust factor | 1.25, assumed | 1.333, measured | Florida ASOS 2-minute pairs, 11 hurricanes | sourced |
| Taper start / cutoff | 200 / 300 km | 259 / 444 km, record quantiles | HURDAT2 34 kt radii | sourced |
| Damage curves | assumed fixtures | unchanged | none available | assumed |

The wind step is now `sourced` end to end and validated against station observations,
including a leave-one-storm-out check that the calibration carries over to storms it
was not fitted on.
The run as a whole stays `assumed` because the damage curves are. "Sourced" means each
number has a documented origin and a reproducible fit; the validation section says how
large the remaining errors are, and they are not small.

Every fixture is produced by a script from committed data, and a test refits each one
and checks the committed values:

```bash
cd backend
python scripts/fit_storm_size.py        # app/fixtures/storm_size_model.json
python scripts/fit_gust_factor.py       # app/fixtures/gust_factor_model.json
python scripts/calibrate_wind_field.py  # app/fixtures/wind_calibration.json
python scripts/validate_wind_field.py   # app/fixtures/wind_validation.json
python -m pytest tests -q
```

## The data

### HURDAT2 wind radii (bundled)

NOAA's best-track file ships inside the `hurricane_simulator` wheel. Beyond the centre
track the simulator uses, each fix from 2004 on records the extent of 34, 50 and 64 kt
winds in four quadrants, and each fix from 2021 on records the radius of maximum wind.
`scripts/fit_storm_size.py` reads these directly (the simulator's loader drops them).

### Florida hurricane ASOS gust data (`data/calibration/fl_hurricane_gust_data`)

One-minute ASOS observations from 46 Florida airport stations during 11 hurricanes,
Hermine 2016 to Milton 2024, built from the NCEI archive via the Iowa Environmental
Mesonet and paired minute by minute with the NHC best track. The calibration-ready
table has 334,762 two-minute windows with a mean of at least 10 kt and clean QC flags.
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
| 34-50 kt | 1.333 (n 7,519) | 1.289 (n 2,716) |
| 50-64 kt | 1.360 (n 285) | 1.320 (n 33) |
| 64 kt or more | 1.424 (n 49) | no data |

The platform value is the land-fetch median, **1.333**, with a 10th-90th percentile
range of 1.23-1.50. The curves are labelled "open terrain", the ASOS siting standard.

Two caveats travel with the number. The observations are ratios to a two-minute mean;
the model's sustained wind follows the best-track one-minute convention. Within a
window the larger of its two one-minute means is at least the two-minute mean, so
G(3s, 2min) is an upper bound on G(3s, 1min), and the two are close in steady hurricane
wind; no conversion factor was applied, because none could be sourced from this
container. And the hurricane-force tail is thin (49 windows), so the value rests on the
34-64 kt bands.

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
wind. That is 156 station-storm pairs from all 11 storms, with 4 truncated records
listed separately.

Objective: minimum mean absolute error among candidates whose median modelled/observed
ratio is within 5% of one overall and within 10% of one where the observed gust was
64 kt or more. The second condition exists because the damage curves only respond above
about 65 kt; the first fit, without it, chose a shape that matched the many moderate
observations by pricing the damaging winds 16% low.

| | Decay | Land factor | Median ratio | MAE | Within 75 km | Beyond 75 km | Observed 64 kt+ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Reference (radii decay, no land factor) | 0.5 | 1.00 | 0.98 | 16.6 kt | 1.23 | 0.90 | 0.87 |
| **Chosen** | **0.275** | **0.75** | 1.03 | 14.1 kt | 1.13 | 0.99 | 0.90 |

### Does it hold up on storms it did not see?

Fitting and judging on the same 156 pairs would let the constants absorb the quirks of
these 11 storms, so the script also refits eleven times, each time without one storm,
and scores that storm with constants that never saw it. Pooled over the held-out
storms:

| | In sample | Out of sample | Uncalibrated reference |
| --- | ---: | ---: | ---: |
| Mean absolute error | 14.1 kt | 15.2 kt | 16.6 kt |
| Median ratio | 1.03 | 1.00 | 0.98 |
| Observed 64 kt or more, median ratio | 0.90 | 0.88 | 0.87 |

The constants chosen without each storm stay within decay 0.25-0.35 and land factor
0.70-0.825, around the in-sample 0.275 and 0.75. So the calibration carries over: a
storm the fit has not met is priced about 8% less accurately than one it has, and
still better than with the original constants. The individual folds show where the
remaining error lives: holding out Michael (4 stations) or Ian leaves them modelled
30-65% high, holding out Irma leaves it 25% low. That is storm-to-storm variation in
size and structure, which no constant can absorb.

The decay exponent is much flatter than the radii-implied 0.49 because observed peak
gusts away from the centre include rainband and convective gusts that a mean profile
does not carry; since the curves consume peak gusts, the peak-gust shape is the one
the pricing needs. The land factor stands in for surface roughness. Both are effective
values, fitted jointly: only their combination is validated, and neither should be
quoted as a physical measurement on its own. Note that the land factor and the gust
factor multiply to 1.00, so the calibrated peak gust at a land station is, on average,
the marine profile's sustained wind. The full candidate grid is in the fixture.

## 4. Validation: what the calibrated step gets right and wrong

`scripts/validate_wind_field.py`, same 156 pairs, calibrated step as the platform runs it:

| Subset | Pairs | Bias | MAE | Median ratio | Within 15% |
| --- | ---: | ---: | ---: | ---: | ---: |
| All | 156 | +3.5 kt | 14.1 kt | 1.03 | 37% |
| Within 75 km of track | 44 | +10.7 kt | 15.4 kt | 1.13 | 41% |
| Beyond 75 km | 112 | +0.6 kt | 13.6 kt | 0.99 | 35% |
| Observed 64 kt or more | 34 | -4.2 kt | 16.1 kt | 0.90 | 35% |

Per storm the median ratio ranges from 0.81 (Nicole) to 1.56 (Michael, 4 stations).
Ian and Matthew run high (1.23, 1.39); Irma runs low (0.83), consistent with Irma
being far larger than the size model gives a 155 kt storm.

What this means for a priced result:

- **Near the track the model is still about 13% high** on average, and the scatter is
  wide: only about a third of stations are within 15% of the model. A single-home
  loss should be read as a central estimate with a spread of that order, not a point
  value.
- **The strongest observed gusts are modelled about 10% low.** At the top of the damage
  curve that is a material fraction of the loss.
- **The four eyewall records that broke off** (Punta Gorda in Ian, Fort Myers and
  RSW in Irma, Sarasota in Milton) all show the model at or above the last observed
  value, as they should, since those observations are lower bounds.
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

The drop is large and comes mostly from the land factor and gust factor together
replacing 1.25 with an effective 1.00 near the track, where the demo homes sit. That
is the direction the stations point: the original constants overstated near-track
gusts by about a quarter against every observed landfall in the set.

## 6. Extending the observations

Eleven storms determine the constants to within the fold spread above; more storms
narrow that and, more importantly, thicken the hurricane-force tail (34 pairs), where
the model still runs 10% low. Two extensions are prepared in
`data/calibration/fl_hurricane_gust_data`; both need a network that can reach the
data hosts, which the environment this branch was built in could not.

### 2004 and 2005 seasons: ready to fetch

NCEI's one-minute ASOS archive begins around 2000, so the two most active Florida
seasons on record are available: Charley, Frances, Ivan and Jeanne in 2004; Dennis,
Katrina, Rita and Wilma in 2005. They add the cases that stress the model most, a very
small Charley and a very large Wilma. `storms_2004_2005.csv` holds their Florida windows
(from the bundled HURDAT2) and `raw/hurdat2_fl_2004_2005.txt` their best-track rows;
`fetch_asos_1min.py` downloads the observations from the Iowa Environmental Mesonet
mirror into the raw file `build.py` reads. Then the whole chain reruns:

```bash
cd data/calibration/fl_hurricane_gust_data
python fetch_asos_1min.py storms_2004_2005.csv storms_2016_2024.csv   # 19 storms
python build.py
cd ../../../backend
python scripts/fit_gust_factor.py && python scripts/calibrate_wind_field.py && python scripts/validate_wind_field.py
python -m pytest tests -q
```

Two things to check on the first run, since the fetch could not be tested here: that
the fetched sections carry the columns `build.py` expects (the script's docstring lists
them), and that the 2004-2005 records do not show the parser misalignment the README
describes for later years, or if they do, that `build.py`'s repair catches them.
HURDAT2 has no radius of maximum wind for these seasons except at landfall, so
`r_over_rmw` will be sparse for them; the calibration does not use it.

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
with the model's field over a grid. Both are a further step once the 2004-2005
airports are in.

To run any of this from a cloud session, the environment's network policy needs these
hosts allowed: `mesonet.agron.iastate.edu`, `www.ncei.noaa.gov`, `fcmp.ce.ufl.edu`,
`www.aoml.noaa.gov`.

## 7. Damage curves: still assumed

Every curve in `backend/app/fixtures/damage_curves.json` derives from the platform's
earlier formula, and the Finance workbook returned no damage-effect evidence.
Calibration needs insurer claims by wind speed and construction class, or published
curves such as the Florida Public Hurricane Loss Model or HAZUS with their evidence
status carried through. Nothing in the repository supplies either. Until then the loss
figures inherit the curve assumption on top of the wind errors above.
