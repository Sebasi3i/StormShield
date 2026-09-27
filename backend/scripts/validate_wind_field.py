"""Validate the wind model against station peak gusts from real Florida hurricanes.

The check the solution plan's item H3 asks for: take each storm's real best track, run
it through the platform's wind step exactly as a catalog storm would be (storm size
from the storm-size model, gust factor from the gust-factor model), and compare the
modelled peak 3-second gust at every ASOS station with the peak gust the station
recorded. Bias and absolute error say how far the modelled hazard sits from what was
observed, for the two constants together.

Inputs, from data/calibration/fl_hurricane_gust_data:

  - best_tracks.csv: the NHC HURDAT2 rows for the 19 storms, wind radii included.
    Only synoptic fixes (00, 06, 12, 18 UTC) are used, because the wind model's track
    validation requires a six-hour cadence; landfall rows at other times are dropped.
  - coverage_by_storm_station.csv: each station's observed peak gust in the storm, and
    whether the record broke off after strong wind (in which case the observed peak is
    a lower bound and the pair is excluded from the statistics).
  - stations.csv: station coordinates and upwind ocean exposure.

Selection: stations whose closest approach to the track is within 250 km and whose
observed peak gust is at least 40 kt, so the comparison is made where wind matters to a
damage curve. Stations on the coast with mostly ocean fetch are kept but marked, since
the gust factor was fitted on land-fetch stations.

Writes app/fixtures/wind_validation.json and prints the tables. Usage, from the backend
directory:

    python scripts/validate_wind_field.py
    python scripts/validate_wind_field.py --gust-factor 1.25   # any alternative factor
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import wind  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
DATASET = BACKEND.parent / "data" / "calibration" / "fl_hurricane_gust_data"
FIXTURE = BACKEND / "app" / "fixtures" / "wind_validation.json"

MAX_APPROACH_KM = 250.0
MIN_OBSERVED_GUST_KT = 40.0
OCEAN_FETCH_FRACTION = 0.5


def storms_from_best_tracks(path: Path) -> list[dict]:
    """The 19 storms in the catalog's track shape, synoptic fixes only."""
    table = pd.read_csv(path, parse_dates=["time_utc"])
    storms = []
    for storm_id, rows in table.groupby("storm_id", sort=False):
        rows = rows[rows["time_utc"].dt.hour.isin([0, 6, 12, 18]) & (rows["time_utc"].dt.minute == 0)]
        rows = rows.sort_values("time_utc")
        storms.append(
            {
                "storm_id": storm_id,
                "storm_name": rows["storm_name"].iloc[0],
                "peak_wind_kt": float(rows["vmax_kt"].max()),
                "track": [
                    {
                        "step": step,
                        "timestamp": row.time_utc.strftime("%Y-%m-%d %H:%M:%S"),
                        "latitude": float(row.lat),
                        "longitude": float(row.lon),
                        "max_wind_kt": float(row.vmax_kt),
                        "category": str(row.status),
                        "is_over_land": False,
                    }
                    for step, row in enumerate(rows.itertuples(index=False))
                ],
            }
        )
    return storms


def _metrics(frame: pd.DataFrame) -> dict:
    if len(frame) == 0:
        return {"n": 0}
    error = frame["modeled_gust_kt"] - frame["observed_gust_kt"]
    ratio = frame["modeled_gust_kt"] / frame["observed_gust_kt"]
    return {
        "n": int(len(frame)),
        "bias_kt": round(float(error.mean()), 1),
        "mean_absolute_error_kt": round(float(error.abs().mean()), 1),
        "median_ratio_modeled_over_observed": round(float(ratio.median()), 3),
        "within_15_percent": round(float(((ratio - 1).abs() <= 0.15).mean()), 2),
        "observed_mean_kt": round(float(frame["observed_gust_kt"].mean()), 1),
        "modeled_mean_kt": round(float(frame["modeled_gust_kt"].mean()), 1),
    }


def validate(gust_factor: float | None = None) -> tuple[dict, pd.DataFrame]:
    storms = storms_from_best_tracks(DATASET / "best_tracks.csv")
    stations = pd.read_csv(DATASET / "stations.csv").set_index("sid")
    coverage = pd.read_csv(DATASET / "coverage_by_storm_station.csv")
    coverage["storm_id"] = coverage["storm_key"].str.split("_").str[0]

    factor = gust_factor if gust_factor is not None else wind.GUST_FACTOR
    coordinates = [(sid, float(row.lat), float(row.lon)) for sid, row in stations.iterrows() if not np.isnan(row.lat)]

    rows = []
    for storm in storms:
        exposures, detail = wind.exposures_for_storm(storm, coordinates)
        size = wind.storm_parameters(storm)
        observed = coverage[coverage["storm_id"] == storm["storm_id"]].set_index("station")
        for exposure, info in zip(exposures, detail):
            if exposure.property_id not in observed.index:
                continue
            obs = observed.loc[exposure.property_id]
            if np.isnan(obs["max_gust_kt"]):
                continue
            # The adapter's gust is mph at wind.GUST_FACTOR; rescale to the factor under test
            # and to knots, the observations' unit.
            modeled_kt = exposure.peak_gust_mph / wind.KT_TO_MPH / wind.GUST_FACTOR * factor
            rows.append(
                {
                    "storm_id": storm["storm_id"],
                    "storm_name": storm["storm_name"],
                    "station": exposure.property_id,
                    "closest_approach_km": info["closest_approach"]["distance_km"],
                    "rmw_km": size["rmw_km"],
                    "observed_gust_kt": float(obs["max_gust_kt"]),
                    "observed_gust_time": obs["time_max_gust"],
                    "modeled_gust_kt": round(modeled_kt, 1),
                    "record_truncated": bool(obs["gap_after_strong_wind"]),
                    "ocean_fetch": bool(stations.loc[exposure.property_id, "dist_to_ocean_km"] == 0)
                    if not np.isnan(stations.loc[exposure.property_id, "dist_to_ocean_km"]) else False,
                }
            )
    table = pd.DataFrame(rows)
    selected = table[
        (table["closest_approach_km"] <= MAX_APPROACH_KM)
        & (table["observed_gust_kt"] >= MIN_OBSERVED_GUST_KT)
        & (~table["record_truncated"])
    ]
    near = selected[selected["closest_approach_km"] <= 75.0]
    far = selected[selected["closest_approach_km"] > 75.0]
    strong = selected[selected["observed_gust_kt"] >= 64.0]

    per_storm = {
        name: _metrics(group) for name, group in selected.groupby("storm_name")
    }
    summary = {
        "wind_validation_id": "fl-asos-hurricane-peaks-v1",
        "validated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "backend/scripts/validate_wind_field.py",
        "gust_factor": factor,
        "storm_size_model_id": wind.load_storm_size_model()["storm_size_model_id"],
        "selection": {
            "max_closest_approach_km": MAX_APPROACH_KM,
            "min_observed_gust_kt": MIN_OBSERVED_GUST_KT,
            "truncated_records_excluded": True,
            "storms": int(selected["storm_id"].nunique()),
            "station_storm_pairs": int(len(selected)),
            "pairs_excluded_as_truncated": int(
                ((table["closest_approach_km"] <= MAX_APPROACH_KM)
                 & (table["observed_gust_kt"] >= MIN_OBSERVED_GUST_KT)
                 & table["record_truncated"]).sum()
            ),
        },
        "all": _metrics(selected),
        "within_75_km": _metrics(near),
        "beyond_75_km": _metrics(far),
        "observed_64kt_or_more": _metrics(strong),
        "per_storm": per_storm,
        "truncated_records": [
            {k: (row[k] if k != "observed_gust_time" else str(row[k])) for k in
             ("storm_name", "station", "closest_approach_km", "observed_gust_kt", "modeled_gust_kt")}
            for _, row in table[table["record_truncated"]].iterrows()
        ],
        "note": (
            "Modeled gust is the platform wind step (storm-size model, modified-Rankine "
            "profile, gust factor) applied to the NHC best track at synoptic fixes; "
            "observed is the station's peak 3-second gust in the storm window. Stations "
            "whose record broke off after strong wind are listed but excluded, since "
            "their observed peak is a lower bound."
        ),
    }
    return summary, selected.sort_values(["storm_name", "closest_approach_km"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gust-factor", type=float, default=None)
    parser.add_argument("--fixture", default=str(FIXTURE))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    summary, table = validate(args.gust_factor)
    pd.set_option("display.width", 200)
    print(f"gust factor {summary['gust_factor']}, size model {summary['storm_size_model_id']}")
    print(f"{summary['selection']['station_storm_pairs']} station-storm pairs from {summary['selection']['storms']} storms; "
          f"{summary['selection']['pairs_excluded_as_truncated']} excluded as truncated\n")
    for key in ("all", "within_75_km", "beyond_75_km", "observed_64kt_or_more"):
        print(f"{key:22s} {summary[key]}")
    print("\nper storm:")
    for name, metrics in summary["per_storm"].items():
        print(f"  {name:8s} {metrics}")
    print("\nclosest 15 pairs:")
    print(table.sort_values("closest_approach_km").head(15)[
        ["storm_name", "station", "closest_approach_km", "rmw_km", "observed_gust_kt", "modeled_gust_kt"]
    ].to_string(index=False))
    print("\ntruncated (observed is a lower bound):")
    for row in summary["truncated_records"]:
        print("  ", row)
    if not args.no_write:
        Path(args.fixture).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.fixture}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
