"""Merge a partial build (out/) into the committed calibration tables in this folder.

build.py processes each storm independently (QC, pairing and coverage are all
grouped by storm and station), so building the 2004-2005 storms on their own and
appending them to the committed 11-storm tables gives the same rows as rebuilding
all 19 storms from one raw file. This script does that append:

    python fetch_asos_1min.py storms_2004_2005.csv   # or use a raw file fetched elsewhere
    python build.py                                   # writes out/ for those storms
    python merge_extension.py                         # folds out/ into the committed tables

Storms present in out/ replace any rows the committed tables already hold for them,
so rerunning is safe. best_tracks.csv is taken from out/ whole, since build.py parses
every bundled HURDAT2 file regardless of which storms the raw file holds.
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"


def replace_storms(committed: pd.DataFrame, new: pd.DataFrame, key: str) -> pd.DataFrame:
    keep = committed[~committed[key].isin(new[key].unique())]
    return pd.concat([keep, new], ignore_index=True)


def main() -> None:
    pairs = replace_storms(
        pd.read_csv(ROOT / "fl_gust_pairs_2min_qc.csv.gz", keep_default_na=True),
        pd.read_csv(OUT / "fl_gust_pairs_2min_qc.csv.gz", keep_default_na=True),
        "storm_id",
    ).sort_values(["storm_id", "station", "time_utc"], kind="stable")
    pairs.to_csv(ROOT / "fl_gust_pairs_2min_qc.csv.gz", index=False, compression="gzip")

    cov = replace_storms(
        pd.read_csv(ROOT / "coverage_by_storm_station.csv"),
        pd.read_csv(OUT / "coverage_by_storm_station.csv"),
        "storm_key",
    ).sort_values(["storm_key", "max_mean_kt"], ascending=[True, False], kind="stable")
    cov.to_csv(ROOT / "coverage_by_storm_station.csv", index=False)

    # Station summary columns are aggregates over coverage: recompute them from the merged table.
    stations = pd.read_csv(OUT / "stations.csv")
    summary_cols = ["storms_with_data", "max_mean_kt", "max_gust_kt", "pairs_ge34kt"]
    stations = stations.drop(columns=summary_cols)
    st_cov = cov.groupby("station").agg(
        storms_with_data=("storm_key", "nunique"), max_mean_kt=("max_mean_kt", "max"),
        max_gust_kt=("max_gust_kt", "max"), pairs_ge34kt=("n_pairs_ge34kt", "sum"),
    )
    stations = stations.merge(st_cov, left_on="sid", right_index=True, how="left")
    stations["storms_with_data"] = stations["storms_with_data"].fillna(0).astype(int)
    stations.to_csv(ROOT / "stations.csv", index=False)

    (ROOT / "best_tracks.csv").write_bytes((OUT / "best_tracks.csv").read_bytes())
    (ROOT / "station_ocean_fetch_by_sector.csv").write_bytes((OUT / "station_ocean_fetch_by_sector.csv").read_bytes())

    print(f"pairs {len(pairs)} rows, {pairs['storm_id'].nunique()} storms; coverage {len(cov)} rows")


if __name__ == "__main__":
    main()
