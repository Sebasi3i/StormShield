# Florida hurricane ASOS gust data (StormShield gust synthesis)

One-minute ASOS wind observations from Florida stations during 19 hurricanes that
affected Florida: 11 from 2016 to 2024, and 8 from the 2004 and 2005 seasons added on
2026-09-27 (see the second table). Each observation is paired with the NHC best
track (distance from the storm center, intensity, size) and a simple ocean-exposure
proxy for the station. Built on 2026-09-26.

## Storms and windows (UTC)

| Storm | Window | Notes |
|---|---|---|
| Hermine 2016 (AL092016) | Aug 31 12Z to Sep 2 18Z | Big Bend Cat 1 |
| Matthew 2016 (AL142016) | Oct 6 12Z to Oct 8 12Z | Stayed just offshore of the east coast |
| Irma 2017 (AL112017) | Sep 9 12Z to Sep 11 18Z | Keys and SW Florida, Cat 4/3 |
| Michael 2018 (AL142018) | Oct 9 18Z to Oct 11 06Z | Panhandle, Cat 5 |
| Sally 2020 (AL192020) | Sep 15 00Z to Sep 17 00Z | Pensacola, Cat 2 |
| Ian 2022 (AL092022) | Sep 27 12Z to Sep 30 00Z | SW Florida, Cat 4 |
| Nicole 2022 (AL172022) | Nov 9 12Z to Nov 11 06Z | East coast, Cat 1 |
| Idalia 2023 (AL102023) | Aug 29 12Z to Aug 31 00Z | **Archive ends Aug 30 05:35Z, before the 11:45Z landfall** |
| Debby 2024 (AL042024) | Aug 4 06Z to Aug 6 06Z | Big Bend, Cat 1 |
| Helene 2024 (AL092024) | Sep 26 06Z to Sep 27 12Z | Big Bend, Cat 4. Archive thins after Sep 27 09Z |
| Milton 2024 (AL142024) | Oct 9 06Z to Oct 11 00Z | Siesta Key, Cat 3. About a quarter of stations drop out after Oct 10 03Z, nearly all after 14Z |

Every Florida ASOS station with one-minute data was requested, which gave 39 to 46
stations per storm.

| Storm (added 2026-09-27) | Window | Stations with data |
|---|---|---|
| Charley 2004 (AL032004) | Aug 12 18Z to Aug 14 12Z | 12 |
| Frances 2004 (AL062004) | Sep 4 00Z to Sep 7 18Z | 12 |
| Ivan 2004 (AL092004) | Sep 14 06Z to Sep 17 06Z | 12 |
| Jeanne 2004 (AL112004) | Sep 25 12Z to Sep 27 18Z | 12 |
| Dennis 2005 (AL042005) | Jul 8 18Z to Jul 11 06Z | 37 |
| Katrina 2005 (AL122005) | Aug 25 00Z to Aug 28 18Z | 37 |
| Rita 2005 (AL182005) | Sep 20 00Z to Sep 22 18Z | 37 |
| Wilma 2005 (AL252005) | Oct 23 12Z to Oct 25 00Z | 37 |

Fewer airports archived one-minute data in those years. The 76 stations whose archives
begin by 2005 were requested; the windows are in `storms_2004_2005.csv`.

## Files

| File | What it is |
|---|---|
| `fl_gust_pairs_2min_qc.csv.gz` | **Calibration-ready pairs.** 526,303 rows (334,762 from 2016-2024, 191,541 from 2004-2005): every even minute (non-overlapping 2-min windows), mean wind at least 10 kt, all QC flags clear. |
| `fl_gust_1min.parquet` | Full continuous 1-minute table, 1,086,764 rows for the 2016-2024 storms and 636,628 for 2004-2005 (not committed), including flagged and missing rows. Use this for gust time-series structure such as autocorrelation. |
| `coverage_by_storm_station.csv` | Data completeness, longest gap, peak mean and gust with times, closest approach, and pair counts for each storm and station. |
| `stations.csv` | Station location, elevation, distance to ocean, and per-station totals. |
| `station_ocean_fetch_by_sector.csv` | Upwind ocean fraction for each station in 36 sectors of 10°. |
| `best_tracks.csv` | NHC HURDAT2 rows used (file `hurdat2-1851-2025-091226.txt`). |
| `build.py`, `raw/` | Script that rebuilds everything. Put `fl_hurricanes_asos1min_raw.csv.gz` (in your Downloads folder) into `raw/` first. It writes to `out/`. |
| `fetch_asos_1min.py`, `merge_extension.py` | Fetch a raw file for a storm manifest from the Iowa Mesonet; fold a partial build in `out/` into the committed tables. |

## Key columns

- `mean2min_kt`, `mean2min_dir_deg`: ASOS 2-minute mean wind speed and direction, reported every minute. Speeds are integer knots.
- `gust1min_peak_kt`: the peak gust within that single minute. Sonic anemometers, now standard at ASOS sites, report a 3-s gust, the convention the NOAA/AOML (Kaplan) hurricane gust study assumes; NCEI's older format documentation calls it a 5-s peak. Neither sensor type nor verified gust duration is recorded per station or per storm in this dataset, so which convention actually applies to a given observation here is assumed, not confirmed - a correction previously stated as settled fact. Treat the reporting era (pre- versus post-2016, roughly tracking ASOS's sonic-anemometer rollout) as a rough temporal proxy for which convention is more likely, never as a substitute for a verified per-station sensor record; `backend/scripts/calibration_common.py`'s `policy.note` on each gust-factor fixture states this same caveat in the numbers that come from this file. Check NCEI's current documentation if the exact duration matters for a specific station.
- `gust_2min_kt` = max(gust at t, gust at t-1 min): the peak gust over the same 2-minute window as the mean. This follows the same alignment as the AOML study. It is NaN if the previous minute is missing.
- `gf_3s_2min` = `gust_2min_kt / mean2min_kt`.
- Storm geometry is interpolated linearly from the best track to each minute:
  - `dist_km`: station distance from the storm center.
  - `azimuth_from_center_deg`: bearing of the station as seen from the center.
  - `rel_azimuth_deg`: bearing relative to the storm's direction of motion (0 = ahead, 90 = right of track).
  - `storm_heading_deg`, `storm_speed_kt`: storm motion, from the center position 90 min before and after.
  - `vmax_kt`, `pmin_mb`, `status`: storm intensity and classification.
  - `rmw_nm`, `r_over_rmw`: radius of maximum wind and the station's distance as a multiple of it.
  - `r34/50/64_quad_nm`: best-track wind radii for the quadrant the station is in.
- `ocean_frac_0_3km`, `ocean_frac_0_10km`, `ocean_frac_0_20km`: fraction of ocean pixels upwind of the station (in the direction the wind comes from) on a 1-km land/ocean mask. `dist_to_ocean_km`: distance to the nearest ocean pixel.
- `field_shift_recovered`: the row was repaired from a parser misalignment (see below).
- `qc_*` flags:
  - `qc_no_mean`, `qc_no_gust_pair`: the value is missing.
  - `qc_gust_lt_mean`: the 2-min mean is higher than the peak gust in the same window, which is physically impossible.
  - `qc_gust_spike`: gust jumps at least 25 kt above both neighbouring minutes.
  - `qc_mean_spike`: 2-min mean jumps at least 20 kt above both neighbouring minutes.
  - `qc_gf_extreme`: gust factor below 1.0 or above 2.5 when the mean is at least 10 kt.

## Gust factor summary (pairs file)

This table is the 2016-2024 storms only (334,762 of the 526,303 rows in the pairs
file) - it predates the 2004-2005 rows added 2026-09-27 below and was never
recomputed over the full file. It happens to line up with what the fitting scripts
now call the `primary_2016_2024` cohort (`backend/scripts/calibration_common.py`),
the one used for production, so it is left as is and relabeled rather than replaced:

| 2-min mean | Pairs | Median G(3s,2min) | 10th to 90th percentile |
|---|---|---|---|
| 10 to 20 kt | 246,121 | 1.375 | 1.22 to 1.62 |
| 20 to 34 kt | 77,646 | 1.350 | 1.23 to 1.55 |
| 34 to 50 kt | 10,628 | 1.318 | 1.21 to 1.48 |
| 50 to 64 kt | 318 | 1.356 | 1.24 to 1.48 |
| 64 kt or more | 49 | 1.424 | 1.35 to 1.52 |

With a mean of at least 20 kt, the median is 1.37 for land-fetch winds and 1.32 when
most of the upwind 10 km is ocean.

**Full current file, all 19 storms** (526,303 rows) - noticeably lower than the table
above, because the 2004-2005 storms alone (below) gust distinctly less:

| 2-min mean | Pairs | Median G(3s,2min) | 10th to 90th percentile |
|---|---|---|---|
| 10 to 20 kt | 400,359 | 1.312 | 1.18 to 1.54 |
| 20 to 34 kt | 110,741 | 1.320 | 1.19 to 1.52 |
| 34 to 50 kt | 14,337 | 1.297 | 1.19 to 1.46 |
| 50 to 64 kt | 774 | 1.283 | 1.18 to 1.44 |
| 64 kt or more | 92 | 1.343 | 1.17 to 1.48 |

**2004-2005 storms only** (191,541 rows):

| 2-min mean | Pairs | Median G(3s,2min) | 10th to 90th percentile |
|---|---|---|---|
| 10 to 20 kt | 154,238 | 1.250 | 1.14 to 1.40 |
| 20 to 34 kt | 33,095 | 1.240 | 1.15 to 1.39 |
| 34 to 50 kt | 3,709 | 1.239 | 1.16 to 1.36 |
| 50 to 64 kt | 456 | 1.245 | 1.16 to 1.36 |
| 64 kt or more | 43 | 1.215 | 1.15 to 1.30 |

None of these three tables is "the" gust factor: which one a fitting script uses
depends on the `--cohort` it is given, and `docs/calibration.md` sections 2-4 explain
why production uses the first one alone rather than the full file.

## Things to know before fitting

1. **Averaging period.** These are G(3s, 2min) values. Your model uses 1-minute sustained winds. A 2-minute window has more chances to contain a peak than a 1-minute window, so G(3s,2min) runs slightly higher than G(3s,1min). Convert the averaging period (for example with the WMO guidelines of Harper, Kepert & Ginger, 2010) rather than applying these ratios to 1-minute winds directly.
2. **The hurricane-force tail is thin.** Only 367 clean pairs have a mean of at least 50 kt, and only 49 have at least 64 kt. Stations near the eyewall usually stop reporting:
   - Ian at Punta Gorda (PGD): mean 76 kt, gust 117 kt at 20:13Z, then silent for about 21 hours. This matches the NHC Tropical Cyclone Report.
   - Milton at Sarasota (SRQ): gust 89 kt, then a long gap.
   - Irma at Naples (APF): stops at 18:42Z, before the eye arrived.
   - Irma at Fort Myers Page Field (FMY): stops at 22:51Z with the 2-min mean still above 50 kt.

   The `gap_after_strong_wind` column in the coverage file marks these cases. A station's largest *available* value is often not its true maximum.
3. **125-kt cap.** Iowa State's parser discards any speed above 125 kt, so the strongest gusts can appear as missing values.
4. **Parser misalignment (repaired).** In 12 stretches, 7,763 rows in total, IEM's parser was off by one field:
   - The mean speed landed in the direction column.
   - The gust landed in the gust-direction column.
   - The runway number landed in the gust column, where it stays constant.

   These rows were rebuilt, and the values join smoothly with the good data on either side. The 2-min mean direction cannot be recovered in these rows. Affected stretches include MIA during Irma (Sep 9 12Z to Sep 10 13Z) and PIE and MIA during Ian. Filter on `field_shift_recovered` if you would rather exclude them.
5. **Low-speed quantization.** Speeds are whole knots, so gust factors at 10 to 20 kt are coarse. For fitting, consider using only means of 20 kt or more.
6. **Exposure is only a proxy.** The ocean fractions separate marine fetch from land fetch. They do not capture local roughness from trees and buildings. For proper roughness, join a land-cover-based roughness length such as NLCD.
7. **RMW is missing for older storms.** HURDAT2 gives a radius of maximum wind at every time step only for the 2022 to 2024 storms here (Ian, Nicole, Idalia, Debby, Helene, Milton). The 2016 to 2020 storms have it only at landfall points, so `r_over_rmw` is mostly NaN for them.

## Sources

- ASOS one-minute data: NCEI archive, accessed through the Iowa Environmental Mesonet service at https://mesonet.agron.iastate.edu/request/asos/1min.phtml
- Best tracks: NHC HURDAT2, https://www.nhc.noaa.gov/data/hurdat/
- Gust pairing method: NOAA/AOML ASOS gust README, https://www.aoml.noaa.gov/ftp/hrd/kaplan/zhang/Readme_ASOS_gust.pdf
- Ian check: NHC Tropical Cyclone Report AL092022, https://www.nhc.noaa.gov/data/tcr/AL092022_Ian.pdf
- Land/ocean mask: `global-land-mask` Python package (GLOBE 1-km)
