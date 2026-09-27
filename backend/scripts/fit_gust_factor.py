"""Fit the gust factor from Florida ASOS observations during hurricanes.

The damage curves are defined on a peak 3-second gust; the wind model produces a
sustained wind and multiplies it by a gust factor to get there. Until now that factor
was wind_field's demonstration value, 1.25. This script fits it from
data/calibration/fl_hurricane_gust_data: one-minute ASOS observations at Florida
stations during 11 hurricanes (2016-2024), each a two-minute mean wind paired with the
peak gust in the same window.

What is fitted, and what is not:

  - The observed ratio is G(3s, 2min): peak 3-second gust over the two-minute mean.
    The model's sustained wind is the best-track one-minute convention. Within a
    two-minute window the larger of its two one-minute means is at least the two-minute
    mean, so G(3s, 2min) is an upper bound on G(3s, 1min) for the same window; in
    steady hurricane wind the two are close. The fitted value is reported on its
    observed basis and the bound is recorded rather than converted with a factor this
    script cannot source.
  - Exposure matters. Stations whose upwind 10 km is mostly ocean gust less than
    stations with land fetch. The curves are labelled "open terrain", the ASOS
    siting standard, so the land-fetch median is the platform value and the ocean-fetch
    median is published alongside.
  - Only windows with a two-minute mean of at least 34 kt (tropical-storm force) are
    used, above the range where integer-knot quantisation distorts the ratio, and rows
    the dataset repaired from a parser misalignment are excluded.

Writes app/fixtures/gust_factor_model.json. Usage, from the backend directory:

    python scripts/fit_gust_factor.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
DATASET = BACKEND.parent / "data" / "calibration" / "fl_hurricane_gust_data"
PAIRS = DATASET / "fl_gust_pairs_2min_qc.csv.gz"
FIXTURE = BACKEND / "app" / "fixtures" / "gust_factor_model.json"

MIN_MEAN_KT = 34.0
# Upwind ocean fraction over 10 km at or above this is "ocean fetch".
OCEAN_FETCH_FRACTION = 0.5
BANDS = [(34.0, 50.0), (50.0, 64.0), (64.0, 999.0)]


def _summary(values: pd.Series) -> dict:
    if len(values) == 0:
        return {"n": 0, "median": None, "mean": None, "p10": None, "p90": None}
    return {
        "n": int(len(values)),
        "median": round(float(values.median()), 3),
        "mean": round(float(values.mean()), 3),
        "p10": round(float(values.quantile(0.10)), 3),
        "p90": round(float(values.quantile(0.90)), 3),
    }


def fit(pairs: pd.DataFrame) -> dict:
    usable = pairs[(pairs["mean2min_kt"] >= MIN_MEAN_KT) & (~pairs["field_shift_recovered"].astype(bool))]
    land = usable[usable["ocean_frac_0_10km"] < OCEAN_FETCH_FRACTION]
    ocean = usable[usable["ocean_frac_0_10km"] >= OCEAN_FETCH_FRACTION]

    by_band = {}
    for low, high in BANDS:
        label = f"{low:g}-{high:g} kt" if high < 999 else f"{low:g}+ kt"
        band = usable[(usable["mean2min_kt"] >= low) & (usable["mean2min_kt"] < high)]
        by_band[label] = {
            "all": _summary(band["gf_3s_2min"]),
            "land_fetch": _summary(band[band["ocean_frac_0_10km"] < OCEAN_FETCH_FRACTION]["gf_3s_2min"]),
            "ocean_fetch": _summary(band[band["ocean_frac_0_10km"] >= OCEAN_FETCH_FRACTION]["gf_3s_2min"]),
        }

    per_storm = (
        usable.groupby("storm_name")["gf_3s_2min"]
        .agg(["size", "median"])
        .rename(columns={"size": "n"})
        .round(3)
    )

    return {
        "gust_factor": round(float(land["gf_3s_2min"].median()), 3),
        "gust_factor_basis": "median G(3s, 2min), land-fetch stations, two-minute mean >= 34 kt",
        "land_fetch": _summary(land["gf_3s_2min"]),
        "ocean_fetch": _summary(ocean["gf_3s_2min"]),
        "all_stations": _summary(usable["gf_3s_2min"]),
        "by_band": by_band,
        "per_storm": {name: {"n": int(row["n"]), "median": float(row["median"])} for name, row in per_storm.iterrows()},
        "selection": {
            "min_mean2min_kt": MIN_MEAN_KT,
            "ocean_fetch_fraction_0_10km": OCEAN_FETCH_FRACTION,
            "parser_repaired_rows_excluded": True,
            "windows_used": int(len(usable)),
            "stations": int(usable["station"].nunique()),
            "storms": int(usable["storm_id"].nunique()),
        },
    }


def build_model(pairs_path: Path) -> dict:
    pairs = pd.read_csv(pairs_path)
    fitted = fit(pairs)
    return {
        "gust_factor_model_id": "fl-asos-hurricane-gust-v1",
        "evidence_status": "sourced",
        "gust_duration_seconds": 3,
        "reference_height_m": 10,
        "terrain_exposure": "open (ASOS siting standard)",
        "sustained_basis_note": (
            "Observed against a two-minute mean. The platform's sustained wind is the "
            "best-track one-minute convention; G(3s, 2min) is an upper bound on "
            "G(3s, 1min) for the same window (the larger one-minute mean is at least the "
            "two-minute mean), and the two are close in steady hurricane wind. No "
            "averaging-period conversion factor has been applied."
        ),
        "source": {
            "dataset": "Florida hurricane ASOS gust data (data/calibration/fl_hurricane_gust_data)",
            "file": pairs_path.name,
            "observations": "NCEI ASOS one-minute data via the Iowa Environmental Mesonet",
            "storms": "Hermine 2016 to Milton 2024, 11 hurricanes, 46 stations",
            "pairing": "NOAA/AOML ASOS gust method: peak gust over the same two-minute window as the mean",
        },
        "fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "backend/scripts/fit_gust_factor.py",
        **fitted,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pairs", nargs="?", default=str(PAIRS))
    parser.add_argument("--fixture", default=str(FIXTURE))
    args = parser.parse_args(argv)

    model = build_model(Path(args.pairs))
    Path(args.fixture).write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")

    sel = model["selection"]
    print(f"{sel['windows_used']} windows >= {MIN_MEAN_KT:g} kt from {sel['stations']} stations, {sel['storms']} storms")
    print(f"gust factor {model['gust_factor']} (land fetch, n={model['land_fetch']['n']}, p10-p90 {model['land_fetch']['p10']}-{model['land_fetch']['p90']})")
    print(f"ocean fetch {model['ocean_fetch']['median']} (n={model['ocean_fetch']['n']})")
    for band, stats in model["by_band"].items():
        print(f"  {band:10s} land {stats['land_fetch']['median']} (n={stats['land_fetch']['n']})  ocean {stats['ocean_fetch']['median']} (n={stats['ocean_fetch']['n']})")
    print(f"wrote {args.fixture}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
