"""Validate the wind model against station peak gusts from real Florida hurricanes.

Runs each storm's real best track through the platform's wind step exactly as a
catalog storm would be, and compares the modelled peak 3-second gust at every ASOS
station with the peak gust the station recorded. `validate()` takes an explicit
parameter bundle (gust factor, decay, land factor) and an explicit storm selection
(cohort or storm_ids), so it can score ANY jointly-fitted combination against ANY
storm set - the production primary bundle on its own storms, a frozen bundle on
storms it never saw, or an alternative bundle on all 19 - without mutating
app/wind.py's module state or overwriting its fixtures. A plain no-argument call
still validates the production primary bundle, unchanged from before this existed.

`build_validation_report()` assembles four distinct validation types and never
conflates them:

  - primary_fitted: the production bundle, scored on the same primary-cohort pairs
    it was fitted from. In-sample - it is not evidence the bundle generalises.
  - primary_holdout: republished from the primary calibration's own leave-one-storm-out
    cross-validation (wind_calibration.json), which refits the gust factor, decay AND
    land factor per fold - not recomputed here, so there is exactly one implementation
    of that method. Out-of-sample, and conditional on the frozen HURDAT2 size model.
  - legacy_frozen_primary: the production bundle, FROZEN (no refitting at all), scored
    on the 8 legacy (2004-2005) storms it has never seen and was never fitted on.
  - combined_sensitivity: an alternative bundle (typically fitted on all 19 storms),
    scored on all 19 storms, for comparison with the primary numbers above. Only
    present when a combined calibration is supplied.

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
    python scripts/validate_wind_field.py --gust-factor 1.25   # override within the default bundle
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import wind  # noqa: E402
import calibration_common as cc  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES_DIR = BACKEND / "app" / "fixtures"
DATASET = BACKEND.parent / "data" / "calibration" / "fl_hurricane_gust_data"
FIXTURE = FIXTURES_DIR / "wind_validation.json"

WIND_VALIDATION_ID = "fl-asos-hurricane-peaks-v2"
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
    core = cc.metrics_from_columns(frame)
    if core["n"] == 0:
        return {"n": 0}
    return {
        "n": core["n"],
        "bias_kt": round(core["bias_kt"], 1),
        "mean_absolute_error_kt": round(core["mean_absolute_error_kt"], 1),
        "median_ratio_modeled_over_observed": round(core["median_ratio"], 3),
        "within_15_percent": round(core["within_15_percent"], 2),
        "observed_mean_kt": round(core["observed_mean_kt"], 1),
        "modeled_mean_kt": round(core["modeled_mean_kt"], 1),
    }


def production_bundle() -> dict:
    """The bundle a no-argument call validates: exactly what app/wind.py runs today."""
    calibration = wind.load_wind_calibration()
    return {
        "gust_factor": wind.GUST_FACTOR,
        "land_exposure_factor": calibration["land_exposure_factor"],
        "outer_decay_exponent": calibration["outer_decay_exponent"],
        "wind_calibration_id": calibration["wind_calibration_id"],
    }


def bundle_from_calibration(calibration: dict) -> dict:
    """The bundle a scripts/calibrate_wind_field.py result implies, for scoring it
    against a different storm set than the one it was fitted on (legacy, combined)."""
    return {
        "gust_factor": calibration["gust_factor"],
        "land_exposure_factor": calibration["land_exposure_factor"],
        "outer_decay_exponent": calibration["outer_decay_exponent"],
        "wind_calibration_id": calibration["wind_calibration_id"],
    }


def validate(
    bundle: dict | None = None,
    *,
    cohort: str | None = None,
    storm_ids: set[str] | None = None,
) -> tuple[dict, pd.DataFrame]:
    """Score `bundle` (default: the production bundle) against `cohort`'s storms, or
    an explicit `storm_ids` set (default: every storm, i.e. no restriction). Give at
    most one of `cohort` / `storm_ids`.

    Runs wind.exposures_for_storm with the bundle passed straight through - no
    post-hoc rescaling - so decay and land factor are honoured exactly like gust
    factor, unlike the single-field override this function used before.
    """
    if cohort is not None and storm_ids is not None:
        raise ValueError("give at most one of cohort, storm_ids")
    bundle = bundle or production_bundle()

    storms = storms_from_best_tracks(DATASET / "best_tracks.csv")
    if cohort is not None:
        storm_ids = cc.cohort_storm_ids(cohort)
    if storm_ids is not None:
        storms = [s for s in storms if s["storm_id"] in storm_ids]
        if not storms:
            raise cc.DataValidationError(f"no storms matched the requested selection (cohort={cohort!r})")

    stations = pd.read_csv(DATASET / "stations.csv").set_index("sid")
    coverage = pd.read_csv(DATASET / "coverage_by_storm_station.csv")
    coverage["storm_id"] = coverage["storm_key"].str.split("_").str[0]

    coordinates = [(sid, float(row.lat), float(row.lon)) for sid, row in stations.iterrows() if not np.isnan(row.lat)]

    rows = []
    for storm in storms:
        exposures, detail = wind.exposures_for_storm(storm, coordinates, bundle=bundle)
        observed = coverage[coverage["storm_id"] == storm["storm_id"]].set_index("station")
        for exposure, info in zip(exposures, detail):
            if exposure.property_id not in observed.index:
                continue
            obs = observed.loc[exposure.property_id]
            if np.isnan(obs["max_gust_kt"]):
                continue
            # The bundle's gust factor, decay and land factor are already baked into
            # exposure.peak_gust_mph by wind.exposures_for_storm; convert units only.
            modeled_kt = exposure.peak_gust_mph / wind.KT_TO_MPH
            rows.append(
                {
                    "storm_id": storm["storm_id"],
                    "storm_name": storm["storm_name"],
                    "station": exposure.property_id,
                    "closest_approach_km": info["closest_approach"]["distance_km"],
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

    per_storm = {name: _metrics(group) for name, group in selected.groupby("storm_name")}
    summary = {
        "gust_factor": bundle["gust_factor"],
        "outer_decay_exponent": bundle["outer_decay_exponent"],
        "land_exposure_factor": bundle["land_exposure_factor"],
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
            "profile, gust factor, decay and land factor from the given bundle) applied "
            "to the NHC best track at synoptic fixes; observed is the station's peak "
            "3-second gust in the storm window. Stations whose record broke off after "
            "strong wind are listed but excluded, since their observed peak is a lower "
            "bound."
        ),
    }
    return summary, selected.sort_values(["storm_name", "closest_approach_km"])


def build_validation_report(
    *,
    primary_calibration: dict | None = None,
    combined_calibration: dict | None = None,
) -> dict:
    """The four validation types, assembled and explicitly labelled - see module
    docstring. `primary_calibration` / `combined_calibration` are
    scripts/calibrate_wind_field.py results (a dict, not a path); primary defaults to
    the committed production fixture."""
    primary_calibration = primary_calibration or json.loads((FIXTURES_DIR / "wind_calibration.json").read_text(encoding="utf-8"))
    primary_bundle = bundle_from_calibration(primary_calibration)

    primary_fitted, _ = validate(primary_bundle, cohort=cc.PRIMARY_COHORT)
    legacy_frozen, _ = validate(primary_bundle, cohort=cc.LEGACY_COHORT)

    report = {
        "wind_validation_id": WIND_VALIDATION_ID,
        "validated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "backend/scripts/validate_wind_field.py",
        "primary_fitted": {
            "validation_type": "fitted (in-sample): the production bundle scored on the same primary-cohort pairs it was fitted from",
            **primary_fitted,
        },
        "primary_holdout": {
            "validation_type": (
                "storm holdout (out-of-sample): each primary-cohort storm scored with the "
                "gust factor, decay and land factor refit on the other primary-cohort "
                "storms alone. Conditional on the fixed HURDAT2 size model, which is not "
                "independently held out."
            ),
            "wind_calibration_id": primary_calibration["wind_calibration_id"],
            "cohort_id": primary_calibration["cohort_id"],
            "out_of_sample": primary_calibration["cross_validation"]["out_of_sample"],
            "out_of_sample_mean_of_per_storm_mae_kt": primary_calibration["cross_validation"]["out_of_sample_mean_of_per_storm_mae_kt"],
            "chosen_constants_across_folds": primary_calibration["cross_validation"]["chosen_constants_across_folds"],
        },
        "legacy_frozen_primary": {
            "validation_type": (
                "frozen evaluation: the production (primary) bundle, with no refitting "
                "at all, scored on the 8 legacy (2004-2005) storms it has never seen"
            ),
            **legacy_frozen,
        },
    }
    if combined_calibration is not None:
        combined_bundle = bundle_from_calibration(combined_calibration)
        combined_all, _ = validate(combined_bundle, cohort=cc.COMBINED_COHORT)
        report["combined_sensitivity"] = {
            "validation_type": "an alternative bundle (typically fitted on all 19 storms) scored on all 19 storms",
            "wind_calibration_id": combined_calibration["wind_calibration_id"],
            "cohort_id": combined_calibration["cohort_id"],
            **combined_all,
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gust-factor", type=float, default=None, help="override within the default (production) bundle")
    parser.add_argument("--fixture", default=str(FIXTURE))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument(
        "--combined-calibration",
        default=None,
        help="path to a scripts/calibrate_wind_field.py --cohort combined_2004_2024 result, "
        "to include combined_sensitivity in the report",
    )
    args = parser.parse_args(argv)

    bundle = production_bundle()
    if args.gust_factor is not None:
        bundle = {**bundle, "gust_factor": args.gust_factor}

    combined_calibration = (
        json.loads(Path(args.combined_calibration).read_text(encoding="utf-8"))
        if args.combined_calibration
        else None
    )
    report = build_validation_report(
        primary_calibration={**production_bundle(), **wind.load_wind_calibration()},
        combined_calibration=combined_calibration,
    )
    # A --gust-factor override changes only the ad hoc printed run below, never the
    # production primary_fitted section of the written report (that always reflects
    # the actual production bundle, so the fixture cannot silently record someone's
    # what-if value as the primary result).
    summary, table = validate(bundle, cohort=cc.PRIMARY_COHORT)

    pd.set_option("display.width", 200)
    print(f"gust factor {summary['gust_factor']}, decay {summary['outer_decay_exponent']}, land {summary['land_exposure_factor']}")
    print(f"{summary['selection']['station_storm_pairs']} station-storm pairs from {summary['selection']['storms']} storms; "
          f"{summary['selection']['pairs_excluded_as_truncated']} excluded as truncated\n")
    for key in ("all", "within_75_km", "beyond_75_km", "observed_64kt_or_more"):
        print(f"{key:22s} {summary[key]}")
    print("\nper storm:")
    for name, metrics in summary["per_storm"].items():
        print(f"  {name:8s} {metrics}")
    print("\nclosest 15 pairs:")
    print(table.sort_values("closest_approach_km").head(15)[
        ["storm_name", "station", "closest_approach_km", "observed_gust_kt", "modeled_gust_kt"]
    ].to_string(index=False))
    print("\ntruncated (observed is a lower bound):")
    for row in summary["truncated_records"]:
        print("  ", row)

    print("\nreport sections:", [k for k in report if k.endswith(("fitted", "holdout", "primary", "sensitivity"))])
    if not args.no_write:
        Path(args.fixture).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.fixture}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
