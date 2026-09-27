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

## Extension: 2004 and 2005 seasons (prepared, not yet fetched)

`storms_2004_2005.csv` lists Charley, Frances, Ivan, Jeanne, Dennis, Katrina, Rita and
Wilma with Florida windows derived from the bundled HURDAT2 (centre inside 22.5-32 N,
88.5-78 W, padded six hours; Ivan trimmed to its landfall passage). Their best-track rows
are in `raw/hurdat2_fl_2004_2005.txt`, taken from the same HURDAT2 file as the rest.
`storms_2016_2024.csv` lists the original 11 with the windows from `README.md`.

The one-minute observations for the new storms are not in this folder yet: the
environment this was prepared in could not reach the Iowa Mesonet or NCEI hosts.
`fetch_asos_1min.py` downloads them into the raw file `build.py` reads; run it where
those hosts are reachable, then `build.py`, then the four calibration scripts.
