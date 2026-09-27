# Calibrating the wind and loss constants

Status of each constant the pricing depends on, what data exists to calibrate it, and
what this branch did about it. Prepared 27 September 2026 on the `calibration` branch.

## Summary

| Constant | Before | Now | Data used | Still needed |
| --- | --- | --- | --- | --- |
| Radius of maximum wind | 30 km, every storm | Per storm, from intensity and latitude | HURDAT2 RMW, 2021-2025 | Station-gust validation |
| Outer decay exponent | 0.5 | 0.492, record median | HURDAT2 64 kt radii | Same |
| Taper start / cutoff | 200 / 300 km | 259 / 444 km, record quantiles | HURDAT2 34 kt radii | Same |
| Gust factor | 1.25, assumed | 1.25, assumed | none available here | Station observations, or a sourced convention |
| Damage curves | assumed fixtures | unchanged | none available here | Claims data or published curves |

Storm size is now `sourced` in every response. Everything else keeps its previous
evidence label. The run as a whole is therefore still `assumed`, because the damage
curves and the gust factor are.

## 1. Storm size: fitted from HURDAT2

### The data

NOAA's HURDAT2 best-track file is bundled inside the `hurricane_simulator` wheel in
`backend/vendor/`, so no download was needed. Beyond the centre track the simulator
uses, each fix from 2004 on records the extent of 34, 50 and 64 kt winds in four
quadrants, and each fix from 2021 on records the radius of maximum wind. The simulator's
loader drops these columns; `backend/scripts/fit_storm_size.py` reads them directly and
writes the extracted table to `data/processed/hurdat2_wind_radii.csv` (55,523 fixes,
ignored by git, reproducible from the script).

### The fit

Restricted to hurricane-strength fixes (status HU, at least 64 kt):

- **Radius of maximum wind.** `ln(rmw_km) = 3.515 - 0.0099 * max_wind_kt + 0.0357 * latitude`,
  fitted by least squares on 633 fixes from 39 storms, seasons 2021 to 2025. R² is 0.41
  and the residual standard deviation in log space is 0.49, so an individual storm can
  sit a factor of 1.6 either side of the fit. The result is clamped to 8 to 80 km, the
  range the record spans for hurricanes.
- **Outer decay exponent.** For the modified-Rankine profile the wind model uses,
  `V(r) = Vmax * (rmw / r) ** x`, the exponent that reproduces each fix's 64 kt radius is
  `x = ln(Vmax / 64) / ln(R64 / rmw)`. Its median over 508 fixes is 0.492, with an
  interquartile range of 0.35 to 0.68.
- **Taper.** The largest-quadrant 34 kt radius of 2,568 hurricane fixes has a median of
  259 km and a 90th percentile of 444 km. These become the taper start and cutoff.

Fitted radius of maximum wind at representative points:

| Intensity, latitude | RMW |
| --- | ---: |
| 64 kt at 25 N | 44 km |
| 96 kt at 26 N | 33 km |
| 120 kt at 26 N | 26 km |
| 140 kt at 27 N | 22 km |
| 155 kt at 20 N | 15 km |

For comparison, Hurricane Ian's landfall fix (130 to 140 kt at 26.7 N) carries a
best-track RMW of 20 nautical miles, 37 km, with hurricane-force winds to 30 to 45
nautical miles. The fit gives 22 to 25 km for a storm like it, inside the residual
spread. Michael, Idalia and Milton were recorded with RMWs of 5 to 10 nautical miles.

### How it is applied

`app/wind.py` evaluates the model once per storm at the track's peak-intensity point
and holds the size constant through the event, which the model's scope note states
explicitly. Every loss response now carries `metadata.storm_size`, one row per storm
with the parameters used and the track point they came from, and each row of
`metadata.wind_exposure_detail` repeats the RMW. The demo constant set is no longer
applied anywhere in the platform.

Two consequences of the "peak intensity" choice are worth knowing. A storm that peaks
far out at sea and weakens before landfall is sized from its strongest, smallest state,
so it is modelled tighter at the coast than it probably was. And catalog tracks are
clipped to the map window, so their peak is the peak of the stored stretch. A
time-varying size (a size per track point) is the natural next step and is a small
change to the same function.

### Effect on the demo

Three catalog storms priced against the ten-home demo portfolio, baseline curves, 5%
deductible, gust factor 1.25, before and after:

| Storm | Stored peak | RMW before | RMW after | Portfolio payout before | Portfolio payout after |
| --- | ---: | ---: | ---: | ---: | ---: |
| SYN0155 | 148 kt | 30 km | 18 km | $952,785 | $856,000 |
| SYN0697 | 109 kt | 30 km | 27 km | $110,347 | $98,028 |
| SYN0973 | 144 kt | 30 km | 21 km | $21,032 | $0 |

The direction is what the record implies: the demo constant was on the large side for
major hurricanes, so homes some distance off the track now see less wind. SYN0973's
$21,032 came entirely from Orlando at 142 mph under the 30 km size; at 21 km the gust
there falls to 119 mph and below the deductible.

## 2. Gust factor: not calibrated, and why

The 1.25 ratio converts the model's one-minute sustained wind to the 3-second gust the
damage curves are defined on. Calibrating it needs paired sustained and gust readings
at land stations during hurricane passages, for example NCEI's hourly station records
or the observation tables in NHC tropical cyclone reports. None of that is in the
repository, and the container this branch was built in could not reach
`www.ncei.noaa.gov` or `www.nhc.noaa.gov`: the environment's network policy denied
both hosts. The download and the fit are ready to do as soon as those hosts are
allowed. Until then 1.25 stays labelled `assumed`. It is within the range published in
the WMO tropical-cyclone wind-averaging guidance for open-land exposure, but that
reference should be read and cited before the label changes, not quoted from memory.

## 3. Damage curves: no data on hand

Every curve in `backend/app/fixtures/damage_curves.json` is derived from the platform's
earlier formula, and the file records that the Finance workbook returned no damage-effect
evidence. Calibration needs insurer claims by wind speed and construction class, or the
adoption of published curves such as the Florida Public Hurricane Loss Model or HAZUS
with their evidence status carried through. Nothing in the repository or reachable from
this environment supplies either.

## 4. Validation: the next step once station data is reachable

HURDAT2 already holds the real tracks and radii of Ian (2022), Irma (2017), Michael
(2018), Idalia (2023) and Milton (2024). With station observations from those
landfalls, the check is: run the real track through the wind model with the fitted
size, compare the modelled peak gust at each station with what it recorded, and report
bias and absolute error. That is the physical validation the solution plan's item H3
describes, and it would also settle the gust factor.

To unblock it, allow these hosts in the environment's network settings:

- `www.ncei.noaa.gov` (station records)
- `www.nhc.noaa.gov` (tropical cyclone reports, current HURDAT2)

## Reproducing

```bash
cd backend
python scripts/fit_storm_size.py        # rewrites app/fixtures/storm_size_model.json
python -m pytest tests -q               # includes a test that the fixture matches a refit
```
