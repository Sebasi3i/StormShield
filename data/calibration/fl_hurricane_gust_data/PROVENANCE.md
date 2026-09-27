# Provenance

Received 27 September 2026 as `fl_hurricane_gust_data.zip` from Adryel, built on
26 September 2026 by the `build.py` in this folder from:

- NCEI ASOS one-minute data for Florida stations, accessed through the Iowa
  Environmental Mesonet one-minute service (`raw/fl_hurricanes_asos1min_raw.csv.gz`,
  not committed: about 40 MB and re-downloadable);
- NHC HURDAT2 rows for the 11 storms (`raw/`, committed);
- the `global-land-mask` package for the ocean-fetch proxy.

`fl_gust_1min.parquet` (26 MB, the full one-minute table including flagged rows) is not
committed either; the calibration uses only `fl_gust_pairs_2min_qc.csv.gz`, the
QC-cleaned pairs table. Rebuild both with `build.py` once the raw ASOS file is in `raw/`.

Used by `backend/scripts/fit_gust_factor.py` and `backend/scripts/validate_wind_field.py`.
See `README.md` for the column definitions and the caveats that matter when fitting.

## Extension: 2004 and 2005 seasons (added 27 September 2026)

`storms_2004_2005.csv` lists Charley, Frances, Ivan, Jeanne, Dennis, Katrina, Rita and
Wilma with Florida windows derived from the bundled HURDAT2 (centre inside 22.5-32 N,
88.5-78 W, padded six hours; Ivan trimmed to its landfall passage). Their best-track rows
are in `raw/hurdat2_fl_2004_2005.txt`, taken from the same HURDAT2 file as the rest.
`storms_2016_2024.csv` lists the original 11 with the windows from `README.md`.

The one-minute observations were fetched on 27 September 2026 from the Iowa
Environmental Mesonet one-minute service (`cgi-bin/request/asos1min.py`, the same query
as `fetch_asos_1min.py`), through the Claude desktop app's browser on a local machine
because the cloud environment could not reach the host. The 76 stations in
`stations.csv` whose archives begin by 2005 were requested; 12 returned data for the
2004 storms and 37 for 2005. The eight per-storm responses were assembled into
`raw/fl_hurricanes_asos1min_raw.csv.gz` (4.8 MB, not committed) and built with
`build.py`; `merge_extension.py` then appended the results to the committed tables.
The 2016-2024 rows are byte-identical after the merge. No parser-misalignment runs were
found in the 2004-2005 records.

## Cohort usage (added 27 September 2026)

This section documents how the two manifest files above are used; it changes nothing
about the receipt or build history recorded higher up. `backend/scripts/
calibration_common.py` treats `storms_2016_2024.csv` and `storms_2004_2005.csv` as the
only two authoritative cohort manifests, joined and excluded strictly by `storm_id`:

- **`primary_2016_2024`** - exactly `storms_2016_2024.csv`'s 11 storms. The production
  cohort: every fixture `backend/scripts/build_calibration.py` promotes is fitted on
  this cohort alone.
- **`legacy_2004_2005`** - exactly `storms_2004_2005.csv`'s 8 storms. Used only to
  evaluate the production (primary) bundle frozen, never to fit a bundle of its own and
  never merged into production.
- **`combined_2004_2024`** - the union of both manifests, 19 storms (all of
  `fl_gust_pairs_2min_qc.csv.gz`). An explicit alternative and sensitivity comparison,
  never a silent default; scored by `backend/scripts/compare_calibration_cohorts.py`.

Requesting a cohort id other than these three is an error, not a guess at a storm-year
boundary. See `docs/calibration.md` for what each cohort's fit actually produced and
why production uses the primary cohort alone.
