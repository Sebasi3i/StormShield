"""Calibrate the wind profile's shape against station peak gusts from real hurricanes.

The storm-size model (scripts/fit_storm_size.py) gives each storm a radius of maximum
wind from the HURDAT2 radii, and the gust-factor model (scripts/fit_gust_factor.py)
gives the gust-to-sustained ratio measured at ASOS stations. Two things remain that
neither dataset measures directly, and this script fits them jointly against the
peak gusts the same stations recorded during 11 Florida hurricanes:

  - the outer decay exponent of the profile V(r) = Vmax * (rmw / r) ** x. The HURDAT2
    64 kt radii imply a median of about 0.49 for the sustained mean wind; station
    PEAK gusts far from the centre include rainband and convective gusts the mean
    profile does not carry, so the shape that reproduces observed peak gusts is
    flatter. The damage curves consume peak gusts, so the peak-gust shape is the one
    the platform needs;
  - an open-terrain land exposure factor L applied to the profile's sustained wind.
    The profile is a marine one and the wind model has no land weakening; every priced
    property and every station is on land, so the reduction is calibrated as one
    factor on the sustained wind rather than left implicit in the gust factor.

The search: for each candidate decay exponent, run every storm's real best track
through wind_field with the storm-size model's radius of maximum wind (evaluated at the
storm's peak intensity near Florida) and a unit gust factor; scale by the measured gust
factor and each candidate L; compare with each station's observed peak gust. The
selected pair minimises mean absolute error among candidates whose median
modelled-over-observed ratio is within 5% of one overall AND within 10% of one for
the pairs where the station recorded hurricane-force gusts (64 kt or more). The second
condition matters because the damage curves only respond above about 65 kt: a shape
that fits the many moderate observations by flattening the strong ones would price
the damaging winds low. Every candidate's scores are written alongside the choice.

Selection of pairs: station within 250 km of the track, observed peak gust at least
40 kt, record not broken off after strong wind (see validate_wind_field.py).

Writes app/fixtures/wind_calibration.json. Usage, from the backend directory:

    python scripts/calibrate_wind_field.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from wind_field import WindFieldConfig, compute_property_exposure

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import wind  # noqa: E402
from validate_wind_field import (  # noqa: E402
    DATASET,
    MAX_APPROACH_KM,
    MIN_OBSERVED_GUST_KT,
    storms_from_best_tracks,
)

BACKEND = Path(__file__).resolve().parents[1]
FIXTURE = BACKEND / "app" / "fixtures" / "wind_calibration.json"

DECAY_CANDIDATES = [round(0.25 + 0.025 * i, 3) for i in range(11)]  # 0.25 .. 0.50
LAND_CANDIDATES = [round(0.70 + 0.025 * i, 3) for i in range(13)]  # 0.70 .. 1.00
MAX_MEDIAN_RATIO_BIAS = 0.05
MAX_STRONG_RATIO_BIAS = 0.10


def _config() -> WindFieldConfig:
    return WindFieldConfig(
        run_id="calibration",
        catalog_id="fl-hurricane-best-tracks",
        gust_factor=1.0,
        gust_duration_seconds=3,
        assumption_notes="unit gust factor; scaled afterwards",
    )


def _unit_gust_table(storms: list[dict], stations: pd.DataFrame, coverage: pd.DataFrame, decay: float) -> pd.DataFrame:
    """Modelled peak sustained wind (kt) at every station for every storm, at this decay."""
    size_model = wind.load_storm_size_model()
    property_table = pd.DataFrame(
        {"property_id": stations.index, "latitude": stations["lat"].to_numpy(), "longitude": stations["lon"].to_numpy()}
    )
    rows = []
    for storm in storms:
        track = pd.DataFrame(
            {
                "storm_id": storm["storm_id"],
                "timestamp": pd.to_datetime([p["timestamp"] for p in storm["track"]]),
                "latitude": [p["latitude"] for p in storm["track"]],
                "longitude": [p["longitude"] for p in storm["track"]],
                "max_wind_kt": [p["max_wind_kt"] for p in storm["track"]],
            }
        )
        peak = wind.size_basis_point(storm)
        parameters = pd.DataFrame(
            [
                {
                    "storm_id": storm["storm_id"],
                    "rmw_km": wind.rmw_km(peak["max_wind_kt"], peak["latitude"], size_model),
                    "outer_decay_exponent": decay,
                    "taper_start_km": size_model["taper"]["taper_start_km"],
                    "cutoff_km": size_model["taper"]["cutoff_km"],
                    "parameter_status": "assumed",
                    "source_note": "candidate",
                }
            ]
        )
        result = compute_property_exposure(track, property_table, parameters, _config()).exposures
        observed = coverage[coverage["storm_id"] == storm["storm_id"]].set_index("station")
        for row in result.itertuples(index=False):
            if row.property_id not in observed.index or math.isnan(observed.loc[row.property_id, "max_gust_kt"]):
                continue
            rows.append(
                {
                    "storm_name": storm["storm_name"],
                    "station": row.property_id,
                    "closest_approach_km": float(row.min_sampled_center_distance_km),
                    "sustained_kt": float(row.peak_gust_mph) / wind.KT_TO_MPH,
                    "observed_gust_kt": float(observed.loc[row.property_id, "max_gust_kt"]),
                    "truncated": bool(observed.loc[row.property_id, "gap_after_strong_wind"]),
                }
            )
    table = pd.DataFrame(rows)
    return table[
        (table["closest_approach_km"] <= MAX_APPROACH_KM)
        & (table["observed_gust_kt"] >= MIN_OBSERVED_GUST_KT)
        & (~table["truncated"])
    ]


def _score(table: pd.DataFrame, factor: float) -> dict:
    modeled = table["sustained_kt"] * factor
    ratio = modeled / table["observed_gust_kt"]
    error = modeled - table["observed_gust_kt"]
    near = table["closest_approach_km"] <= 75.0
    strong = table["observed_gust_kt"] >= 64.0
    per_storm = pd.Series(ratio.values, index=table["storm_name"].values).groupby(level=0).median()
    return {
        "median_ratio": round(float(ratio.median()), 3),
        "mean_absolute_error_kt": round(float(error.abs().mean()), 2),
        "within_15_percent": round(float(((ratio - 1).abs() <= 0.15).mean()), 3),
        "median_ratio_within_75_km": round(float(ratio[near].median()), 3),
        "median_ratio_beyond_75_km": round(float(ratio[~near].median()), 3),
        "median_ratio_observed_64kt_or_more": round(float(ratio[strong].median()), 3),
        "per_storm_median_ratio_range": [round(float(per_storm.min()), 3), round(float(per_storm.max()), 3)],
    }


def calibrate() -> dict:
    gust_model = wind.load_gust_factor_model()
    gust_factor = gust_model["gust_factor"]
    storms = storms_from_best_tracks(DATASET / "best_tracks.csv")
    stations = pd.read_csv(DATASET / "stations.csv").set_index("sid")
    stations = stations[stations["lat"].notna()]
    coverage = pd.read_csv(DATASET / "coverage_by_storm_station.csv")
    coverage["storm_id"] = coverage["storm_key"].str.split("_").str[0]

    candidates = []
    pairs = None
    for decay in DECAY_CANDIDATES:
        table = _unit_gust_table(storms, stations, coverage, decay)
        pairs = len(table)
        for land in LAND_CANDIDATES:
            candidates.append(
                {"outer_decay_exponent": decay, "land_exposure_factor": land, **_score(table, gust_factor * land)}
            )

    eligible = [
        c for c in candidates
        if abs(c["median_ratio"] - 1.0) <= MAX_MEDIAN_RATIO_BIAS
        and abs(c["median_ratio_observed_64kt_or_more"] - 1.0) <= MAX_STRONG_RATIO_BIAS
    ]
    chosen = min(eligible, key=lambda c: c["mean_absolute_error_kt"])
    baseline = next(
        c for c in candidates
        if c["outer_decay_exponent"] == 0.5 and c["land_exposure_factor"] == 1.0
    )
    return {
        "wind_calibration_id": "fl-asos-hurricane-peaks-v1",
        "evidence_status": "sourced",
        "calibrated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "backend/scripts/calibrate_wind_field.py",
        "gust_factor_model_id": gust_model["gust_factor_model_id"],
        "gust_factor": gust_factor,
        "storm_size_model_id": wind.load_storm_size_model()["storm_size_model_id"],
        "size_basis": "peak intensity within the Florida window (wind.size_basis_point)",
        "outer_decay_exponent": chosen["outer_decay_exponent"],
        "land_exposure_factor": chosen["land_exposure_factor"],
        "chosen": chosen,
        "objective": (
            f"minimum mean absolute error among candidates with median modelled/observed "
            f"ratio within {MAX_MEDIAN_RATIO_BIAS:.0%} of 1 overall and within "
            f"{MAX_STRONG_RATIO_BIAS:.0%} of 1 where the observed gust was 64 kt or more"
        ),
        "uncalibrated_reference": {
            "note": "decay 0.5 (near the HURDAT2 radii median) and no land factor, at the measured gust factor",
            **baseline,
        },
        "data": {
            "dataset": "data/calibration/fl_hurricane_gust_data",
            "storms": len(storms),
            "station_storm_pairs": pairs,
            "selection": {
                "max_closest_approach_km": MAX_APPROACH_KM,
                "min_observed_gust_kt": MIN_OBSERVED_GUST_KT,
                "truncated_records_excluded": True,
            },
        },
        "grid": {
            "outer_decay_exponent": DECAY_CANDIDATES,
            "land_exposure_factor": LAND_CANDIDATES,
        },
        "candidates": candidates,
        "interpretation": (
            "The decay exponent is flatter than the radii-implied mean-wind value "
            "because observed peak gusts away from the centre include convective gusts "
            "the mean profile lacks; the land factor stands in for the surface "
            "roughness the marine profile ignores. Both are effective, calibrated "
            "values for reproducing station peak gusts, not physical measurements, and "
            "they were fitted jointly: only their combination is validated."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture", default=str(FIXTURE))
    args = parser.parse_args(argv)

    model = calibrate()
    Path(args.fixture).write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    chosen, ref = model["chosen"], model["uncalibrated_reference"]
    print(f"{model['data']['station_storm_pairs']} station-storm pairs, gust factor {model['gust_factor']}")
    print(f"chosen: decay {chosen['outer_decay_exponent']}, land factor {chosen['land_exposure_factor']} -> "
          f"median ratio {chosen['median_ratio']}, MAE {chosen['mean_absolute_error_kt']} kt, "
          f"near {chosen['median_ratio_within_75_km']}, far {chosen['median_ratio_beyond_75_km']}, "
          f">=64kt {chosen['median_ratio_observed_64kt_or_more']}, within 15%: {chosen['within_15_percent']}")
    print(f"reference (decay 0.5, no land factor): median ratio {ref['median_ratio']}, MAE {ref['mean_absolute_error_kt']} kt, "
          f"near {ref['median_ratio_within_75_km']}, far {ref['median_ratio_beyond_75_km']}")
    print(f"wrote {args.fixture}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
