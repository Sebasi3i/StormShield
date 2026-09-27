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
