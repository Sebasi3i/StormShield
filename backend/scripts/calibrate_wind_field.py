"""Calibrate the wind profile's shape against station peak gusts from real hurricanes.

The storm-size model (scripts/fit_storm_size.py) gives each storm a radius of maximum
wind from the HURDAT2 radii. Two things remain that no dataset measures directly, and
this script fits them - and the gust factor alongside them - jointly against the peak
gusts the same stations recorded:

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

Cohort policy: the primary cohort (11 storms, 2016-2024) is the production fit. The
combined cohort (19 storms) is an explicit alternative, built the same way but never
promoted silently - see scripts/calibration_common.py and
scripts/compare_calibration_cohorts.py. A fit for one cohort uses that cohort's own
gust factor, fitted from that cohort's own pairs; it never reads the other cohort's (or
the production fixture's) gust factor off disk, so an alternative fit cannot
accidentally inherit constants it was not supposed to have seen.

The search: for each candidate decay exponent, run every storm's real best track
through wind_field with the storm-size model's radius of maximum wind (evaluated at the
storm's peak intensity near Florida) and a unit gust factor; scale by the cohort's
measured gust factor and each candidate L; compare with each station's observed peak
gust, restricted to the requested cohort's storms. The selected pair minimises mean
absolute error among candidates whose median modelled-over-observed ratio is within 5%
of one overall AND within 10% of one for the pairs where the station recorded
hurricane-force gusts (64 kt or more) - unless a cohort's training subset has no such
pairs, in which case that constraint is reported unevaluable rather than silently
treated as satisfied. Every candidate's scores are written alongside the choice, as
unrounded selection values; only the exported fixture is rounded.

Because the same pairs would otherwise both choose the constants and judge them, the
script also runs a leave-one-storm-out check: for each storm in the cohort it refits
ALL THREE ASOS-derived constants - gust factor, decay, and land factor - on the other
cohort storms alone, and scores the held-out storm with constants that never saw it in
any of the three fits. (Earlier versions of this check refit only decay and land while
reusing a gust factor fitted from every storm, including the held-out one; that let the
held-out storm's own ASOS windows influence the very factor used to judge it.) The
pooled out-of-sample errors, using each fold's own gust factor rather than one global
value, and the spread of the chosen constants across the folds are written with the
result.

The HURDAT2 storm-size fixture is frozen for this script: the size fit is not
independently held out here, and this file's holdout result is conditional on it - see
docs/calibration.md.

Writes app/fixtures/wind_calibration.json. Usage, from the backend directory:

    python scripts/calibrate_wind_field.py
    python scripts/calibrate_wind_field.py --cohort combined_2004_2024 --fixture /tmp/alt.json
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import wind  # noqa: E402
import calibration_common as cc  # noqa: E402
import fit_gust_factor  # noqa: E402
from validate_wind_field import (  # noqa: E402
    DATASET,
    MAX_APPROACH_KM,
    MIN_OBSERVED_GUST_KT,
    storms_from_best_tracks,
)

BACKEND = Path(__file__).resolve().parents[1]
FIXTURE = BACKEND / "app" / "fixtures" / "wind_calibration.json"

WIND_CALIBRATION_ID = "fl-asos-hurricane-peaks-v2"

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
    """Modelled peak sustained wind (kt) at every station for every storm, at this
    decay. Computed over every storm in `storms` regardless of cohort - callers
    subset the result by storm_id, so this expensive step runs once per decay and is
    reused across every cohort and every fold."""
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
                    "storm_id": storm["storm_id"],
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


def all_unit_gust_tables(decays: list[float] = DECAY_CANDIDATES) -> dict[float, pd.DataFrame]:
    """`_unit_gust_table` for every candidate decay, over every one of the 19 storms.
    Public so an orchestrator can compute this once and pass it to several `calibrate`
    calls (primary, combined, and every fold of each) without rerunning wind_field."""
    storms = storms_from_best_tracks(DATASET / "best_tracks.csv")
    stations = pd.read_csv(DATASET / "stations.csv").set_index("sid")
    stations = stations[stations["lat"].notna()]
    coverage = pd.read_csv(DATASET / "coverage_by_storm_station.csv")
    coverage["storm_id"] = coverage["storm_key"].str.split("_").str[0]
    return {decay: _unit_gust_table(storms, stations, coverage, decay) for decay in decays}


def _score_raw(table: pd.DataFrame, factor: float) -> dict:
    """Unrounded scores. A subset with no rows in a required band (strong gusts, or
    the whole table) reports that band as None rather than NaN or a fabricated value -
    'unevaluable', not 'passed'."""
    if len(table) == 0:
        return {
            "n": 0, "median_ratio": None, "mean_absolute_error_kt": None, "within_15_percent": None,
            "median_ratio_within_75_km": None, "median_ratio_beyond_75_km": None,
            "median_ratio_observed_64kt_or_more": None,
            "per_storm_median_ratio_min": None, "per_storm_median_ratio_max": None,
        }
    modeled = table["sustained_kt"] * factor
    ratio = modeled / table["observed_gust_kt"]
    error = modeled - table["observed_gust_kt"]
    near = table["closest_approach_km"] <= 75.0
    strong = table["observed_gust_kt"] >= 64.0
    per_storm = pd.Series(ratio.values, index=table["storm_name"].values).groupby(level=0).median()

    def _median_or_none(selector: pd.Series) -> float | None:
        subset = ratio[selector]
        return float(subset.median()) if len(subset) else None

    return {
        "n": int(len(table)),
        "median_ratio": float(ratio.median()),
        "mean_absolute_error_kt": float(error.abs().mean()),
        "within_15_percent": float(((ratio - 1).abs() <= 0.15).mean()),
        "median_ratio_within_75_km": _median_or_none(near),
        "median_ratio_beyond_75_km": _median_or_none(~near),
        "median_ratio_observed_64kt_or_more": _median_or_none(strong),
        "per_storm_median_ratio_min": float(per_storm.min()) if len(per_storm) else None,
        "per_storm_median_ratio_max": float(per_storm.max()) if len(per_storm) else None,
    }


def _export_score(raw: dict) -> dict:
    """Round only for export; every selection decision upstream used `raw` unrounded."""

    def r(key: str, ndigits: int) -> float | None:
        value = raw[key]
        return None if value is None else round(float(value), ndigits)

    range_ = (
        None
        if raw["per_storm_median_ratio_min"] is None
        else [round(raw["per_storm_median_ratio_min"], 3), round(raw["per_storm_median_ratio_max"], 3)]
    )
    return {
        "n": raw["n"],
        "median_ratio": r("median_ratio", 3),
        "mean_absolute_error_kt": r("mean_absolute_error_kt", 2),
        "within_15_percent": r("within_15_percent", 3),
        "median_ratio_within_75_km": r("median_ratio_within_75_km", 3),
        "median_ratio_beyond_75_km": r("median_ratio_beyond_75_km", 3),
        "median_ratio_observed_64kt_or_more": r("median_ratio_observed_64kt_or_more", 3),
        "per_storm_median_ratio_range": range_,
    }


def _candidates(
    tables: dict[float, pd.DataFrame],
    gust_factor: float,
    storm_ids: set[str],
    *,
    exclude_storm_ids: tuple[str, ...] = (),
) -> list[dict]:
    """Every (decay, land factor) pair scored on this cohort's (minus any excluded
    storm's) rows only. Each candidate carries its raw (unrounded) score for
    selection and its exported (rounded) score for the fixture."""
    keep_ids = set(storm_ids) - set(exclude_storm_ids)
    candidates = []
    for decay, table in tables.items():
        subset = table[table["storm_id"].isin(keep_ids)]
        for land in LAND_CANDIDATES:
            raw = _score_raw(subset, gust_factor * land)
            candidates.append(
                {
                    "outer_decay_exponent": decay,
                    "land_exposure_factor": land,
                    "raw": raw,
                    **_export_score(raw),
                }
            )
    return candidates


def _select(candidates: list[dict]) -> dict:
    """The objective: lowest MAE among candidates unbiased overall and for strong
    gusts, selected on unrounded values. A candidate whose strong-gust band is
    unevaluable (empty) is judged on the overall constraint and MAE alone, not
    treated as passing or failing that band. A training subset with zero rows for
    every candidate (never a partial one, since row membership does not depend on
    decay or land) is a hard failure, not a silent pick."""
    if all(c["raw"]["n"] == 0 for c in candidates):
        raise cc.DataValidationError("no peak comparisons in this training subset for any candidate")

    def excess(c: dict) -> float:
        raw = c["raw"]
        if raw["median_ratio"] is None:
            return math.inf
        total = max(0.0, abs(raw["median_ratio"] - 1.0) - MAX_MEDIAN_RATIO_BIAS)
        if raw["median_ratio_observed_64kt_or_more"] is not None:
            total += max(0.0, abs(raw["median_ratio_observed_64kt_or_more"] - 1.0) - MAX_STRONG_RATIO_BIAS)
        return total

    def mae(c: dict) -> float:
        value = c["raw"]["mean_absolute_error_kt"]
        return math.inf if value is None else value

    eligible = [c for c in candidates if excess(c) == 0.0]
    if eligible:
        chosen = min(eligible, key=mae)
        strong_evaluable = chosen["raw"]["median_ratio_observed_64kt_or_more"] is not None
    else:
        least = min(excess(c) for c in candidates)
        nearest = [c for c in candidates if excess(c) == least]
        chosen = min(nearest, key=mae)
        strong_evaluable = chosen["raw"]["median_ratio_observed_64kt_or_more"] is not None
        chosen = {**chosen, "bounds_relaxed_by": round(least, 3)}
    result = {k: v for k, v in chosen.items() if k != "raw"}
    result["strong_gust_constraint_evaluable"] = strong_evaluable
    return result


def _leave_one_storm_out(
    pairs: pd.DataFrame,
    tables: dict[float, pd.DataFrame],
    storm_ids: set[str],
    chosen: dict,
    cohort_id: str,
) -> dict:
    """Refit gust factor, decay AND land factor without each cohort storm in turn, and
    score that storm with constants that never saw it in any of the three fits.

    Out-of-sample rows carry their own fold's modeled_gust_kt (its own gust factor,
    decay and land factor), never one global factor, so pooling them is not silently
    reusing information a fold was supposed to be blind to.
    """
    folds: list[dict] = []
    held_out_rows: list[pd.DataFrame] = []
    for storm_id in sorted(storm_ids):
        remaining = storm_ids - {storm_id}
        gust_fit = fit_gust_factor.fit(pairs, storm_ids=remaining)
        fold_gust_factor = gust_fit["gust_factor"]
        candidates = _candidates(tables, fold_gust_factor, storm_ids, exclude_storm_ids=(storm_id,))
        fold_choice = _select(candidates)

        held = tables[fold_choice["outer_decay_exponent"]]
        held = held[held["storm_id"] == storm_id].copy()
        if len(held) == 0:
            folds.append({"held_out_storm_id": storm_id, "pairs": 0, "status": "no peak comparisons for this storm"})
            continue

        held["modeled_gust_kt"] = held["sustained_kt"] * fold_gust_factor * fold_choice["land_exposure_factor"]
        held_out_rows.append(held[["storm_id", "storm_name", "station", "observed_gust_kt", "modeled_gust_kt"]])
        fold_metrics = cc.metrics_from_columns(held)
        folds.append(
            {
                "held_out_storm_id": storm_id,
                "pairs": int(len(held)),
                "gust_factor": fold_gust_factor,
                "gust_factor_contributing_storms": gust_fit["selection"]["storms_contributing_usable_observations"],
                "outer_decay_exponent": fold_choice["outer_decay_exponent"],
                "land_exposure_factor": fold_choice["land_exposure_factor"],
                "strong_gust_constraint_evaluable": fold_choice["strong_gust_constraint_evaluable"],
                "bounds_relaxed_by": fold_choice.get("bounds_relaxed_by", 0.0),
                "held_out_median_ratio": (
                    round(fold_metrics["median_ratio"], 3) if fold_metrics["median_ratio"] is not None else None
                ),
                "held_out_mean_absolute_error_kt": (
                    round(fold_metrics["mean_absolute_error_kt"], 2)
                    if fold_metrics["mean_absolute_error_kt"] is not None
                    else None
                ),
            }
        )

    evaluable = [f for f in folds if f["pairs"] > 0]
    if not evaluable:
        raise cc.DataValidationError(f"no evaluable leave-one-out fold for cohort {cohort_id}")

    pooled = pd.concat(held_out_rows, ignore_index=True)
    out_raw = cc.metrics_from_columns(pooled)
    mean_of_per_storm_mae = float(np.mean([f["held_out_mean_absolute_error_kt"] for f in evaluable]))

    decays = [f["outer_decay_exponent"] for f in evaluable]
    lands = [f["land_exposure_factor"] for f in evaluable]
    gusts = [f["gust_factor"] for f in evaluable]
    return {
        "method": (
            "leave one storm out: refit the gust factor, decay and land factor on the "
            "other cohort storms, score the held-out storm with all three"
        ),
        "cohort_id": cohort_id,
        "folds": folds,
        "storms_with_no_evaluable_peak_comparisons": [
            f["held_out_storm_id"] for f in folds if f["pairs"] == 0
        ],
        "out_of_sample": {
            "n": out_raw["n"],
            "mean_absolute_error_kt": round(out_raw["mean_absolute_error_kt"], 2),
            "median_ratio": round(out_raw["median_ratio"], 3),
        },
        "out_of_sample_mean_of_per_storm_mae_kt": round(mean_of_per_storm_mae, 2),
        "in_sample": {
            "n": chosen["n"],
            "mean_absolute_error_kt": chosen["mean_absolute_error_kt"],
            "median_ratio": chosen["median_ratio"],
        },
        "chosen_constants_across_folds": {
            "gust_factor": {"min": min(gusts), "max": max(gusts)},
            "outer_decay_exponent": {
                "min": min(decays), "max": max(decays), "in_sample": chosen["outer_decay_exponent"],
            },
            "land_exposure_factor": {
                "min": min(lands), "max": max(lands), "in_sample": chosen["land_exposure_factor"],
            },
        },
    }


def calibrate(
    cohort: str = cc.PRIMARY_COHORT,
    *,
    tables: dict[float, pd.DataFrame] | None = None,
    pairs: pd.DataFrame | None = None,
) -> dict:
    """Fit decay, land factor, and (via fit_gust_factor) the gust factor, all from
    `cohort`'s own storms - never from the production fixture or another cohort.

    `tables` and `pairs`, if given, are reused rather than recomputed - see
    `all_unit_gust_tables` and `build_calibration.py`. Fresh copies are loaded when
    omitted, so this remains independently callable.
    """
    storm_ids = cc.cohort_storm_ids(cohort)
    pairs = pairs if pairs is not None else pd.read_csv(fit_gust_factor.PAIRS)
    gust_model = fit_gust_factor.build_model(cohort=cohort)
    gust_factor = gust_model["gust_factor"]

    tables = tables if tables is not None else all_unit_gust_tables(DECAY_CANDIDATES)
    cc.validate_cohorts_partition(set(tables[DECAY_CANDIDATES[0]]["storm_id"]))

    candidates = _candidates(tables, gust_factor, storm_ids)
    chosen = _select(candidates)
    cross_validation = _leave_one_storm_out(pairs, tables, storm_ids, chosen, cohort)

    reference_table = tables[0.5][tables[0.5]["storm_id"].isin(storm_ids)]
    baseline_raw = _score_raw(reference_table, gust_factor * 1.0)
    baseline = _export_score(baseline_raw)

    any_table = tables[DECAY_CANDIDATES[0]]
    pairs_count = int(any_table["storm_id"].isin(storm_ids).sum())

    return {
        "wind_calibration_id": WIND_CALIBRATION_ID,
        "cohort_id": cohort,
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
            f"{MAX_STRONG_RATIO_BIAS:.0%} of 1 where the observed gust was 64 kt or more "
            "(the second constraint is skipped, not treated as passed, when a training "
            "subset has no 64 kt+ pairs)"
        ),
        "uncalibrated_reference": {
            "note": "decay 0.5 (near the HURDAT2 radii median) and no land factor, at this cohort's own measured gust factor",
            "outer_decay_exponent": 0.5,
            "land_exposure_factor": 1.0,
            **baseline,
        },
        "data": {
            "dataset": "data/calibration/fl_hurricane_gust_data",
            "storms": len(storm_ids),
            "station_storm_pairs": pairs_count,
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
        "candidates": [{k: v for k, v in c.items() if k != "raw"} for c in candidates],
        "cross_validation": cross_validation,
        "interpretation": (
            "The decay exponent is flatter than the radii-implied mean-wind value "
            "because observed peak gusts away from the centre include convective gusts "
            "the mean profile lacks; the land factor stands in for the surface "
            "roughness the marine profile ignores. Both are effective, calibrated "
            "values for reproducing station peak gusts, not physical measurements, and "
            "they were fitted jointly with the gust factor: only the combination is "
            "validated. A grid-edge choice is a diagnostic to look at, not by itself a "
            "sign the fit is wrong."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cohort", default=cc.PRIMARY_COHORT, choices=cc.COHORT_IDS)
    parser.add_argument("--fixture", default=str(FIXTURE))
    args = parser.parse_args(argv)

    model = calibrate(args.cohort)
    Path(args.fixture).write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    chosen, ref = model["chosen"], model["uncalibrated_reference"]
    print(f"cohort {model['cohort_id']}: {model['data']['station_storm_pairs']} station-storm pairs, gust factor {model['gust_factor']}")
    print(f"chosen: decay {chosen['outer_decay_exponent']}, land factor {chosen['land_exposure_factor']} -> "
          f"median ratio {chosen['median_ratio']}, MAE {chosen['mean_absolute_error_kt']} kt, "
          f"near {chosen['median_ratio_within_75_km']}, far {chosen['median_ratio_beyond_75_km']}, "
          f">=64kt {chosen['median_ratio_observed_64kt_or_more']}, within 15%: {chosen['within_15_percent']}")
    print(f"reference (decay 0.5, no land factor): median ratio {ref['median_ratio']}, MAE {ref['mean_absolute_error_kt']} kt, "
          f"near {ref['median_ratio_within_75_km']}, far {ref['median_ratio_beyond_75_km']}")
    cv = model["cross_validation"]
    oos, ins = cv["out_of_sample"], cv["in_sample"]
    print(f"leave-one-storm-out (gust factor refit per fold too): MAE {oos['mean_absolute_error_kt']} kt out of sample vs "
          f"{ins['mean_absolute_error_kt']} in sample; median ratio {oos['median_ratio']} vs {ins['median_ratio']}; "
          f"mean of per-storm MAE {cv['out_of_sample_mean_of_per_storm_mae_kt']} kt")
    spread = cv["chosen_constants_across_folds"]
    print(f"constants across folds: gust {spread['gust_factor']['min']}-{spread['gust_factor']['max']}, "
          f"decay {spread['outer_decay_exponent']['min']}-{spread['outer_decay_exponent']['max']}, "
          f"land {spread['land_exposure_factor']['min']}-{spread['land_exposure_factor']['max']}")
    for fold in cv["folds"]:
        if fold["pairs"] == 0:
            print(f"  without {fold['held_out_storm_id']:10s}: {fold['status']}")
            continue
        print(f"  without {fold['held_out_storm_id']:10s}: gust {fold['gust_factor']}, decay {fold['outer_decay_exponent']}, "
              f"land {fold['land_exposure_factor']} -> held-out ratio {fold['held_out_median_ratio']}, "
              f"MAE {fold['held_out_mean_absolute_error_kt']} ({fold['pairs']} pairs)")
    print(f"wrote {args.fixture}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
