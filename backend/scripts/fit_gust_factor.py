"""Fit the gust factor from Florida ASOS observations during hurricanes.

The damage curves are defined on a peak 3-second gust; the wind model produces a
sustained wind and multiplies it by a gust factor to get there. Until now that factor
was fitted once from all 19 storms pooled together. This version fits it per cohort:

  - primary_2016_2024 (11 storms) is the production cohort.
  - legacy_2004_2005 (8 storms) is evaluated separately, never silently pooled in.
  - combined_2004_2024 (19 storms, the old behaviour) is an explicit alternative for
    the sensitivity comparison, not a default.

Reporting era is used only as a data-availability grouping. It is not evidence of
sensor type: no correction is derived from it, and none is claimed.

What is fitted, and what is not:

  - The observed ratio is G(3s, 2min): peak 3-second gust over the two-minute mean.
    The model's sustained wind is the best-track one-minute convention. Within a
    two-minute window the larger of its two one-minute means is at least the two-minute
    mean, so G(3s, 2min) is an upper bound on G(3s, 1min) for the same window; in
    steady hurricane wind the two are close. The fitted value is reported on its
    observed basis and the bound is recorded rather than converted with a factor this
    script cannot source. The observed gust *duration* is not independently verified
    for any station in any era; scripts/calibration_common.py's MEASUREMENT_POLICY
    records this as an explicit, unresolved limitation rather than an assumption
    buried in a column name.
  - Exposure matters. Stations whose upwind 10 km is mostly ocean gust less than
    stations with land fetch. The curves are labelled "open terrain", the ASOS
    siting standard, so the land-fetch median is the platform value and the ocean-fetch
    median is published alongside.
  - Only windows with a two-minute mean of at least 34 kt (tropical-storm force) are
    used, above the range where integer-knot quantisation distorts the ratio, and rows
    the dataset repaired from a parser misalignment are excluded.

Writes app/fixtures/gust_factor_model.json. Usage, from the backend directory:

    python scripts/fit_gust_factor.py
    python scripts/fit_gust_factor.py --cohort combined_2004_2024 --fixture /tmp/alt.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calibration_common as cc  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
DATASET = BACKEND.parent / "data" / "calibration" / "fl_hurricane_gust_data"
PAIRS = DATASET / "fl_gust_pairs_2min_qc.csv.gz"
FIXTURE = BACKEND / "app" / "fixtures" / "gust_factor_model.json"

GUST_FACTOR_MODEL_ID = "fl-asos-hurricane-gust-v2"

MIN_MEAN_KT = 34.0
# Upwind ocean fraction over 10 km at or above this is "ocean fetch".
OCEAN_FETCH_FRACTION = 0.5
BANDS = [(34.0, 50.0), (50.0, 64.0), (64.0, 999.0)]
# gf_3s_2min is a stored, rounded copy of gust_2min_kt / mean2min_kt; anything wider
# than this is a data problem, not rounding noise (observed max drift is 0.0005).
MAX_RATIO_DRIFT = 0.01


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


def fit(pairs: pd.DataFrame, *, storm_ids: set[str]) -> dict:
    """Fit the gust factor from exactly `storm_ids`' rows of `pairs`.

    A pure function: no file I/O, callable directly with a fold's storm_ids (see
    calibrate_wind_field.py's per-fold refit) without writing any fixture. `storm_ids`
    is the caller's fully resolved set (a cohort, or a cohort minus a held-out storm);
    this function does not know or care which cohort it came from.
    """
    cc.require_unique_keys(pairs, ["storm_id", "station", "time_utc"])

    exclusions: dict[str, int] = {}
    cohort_rows = cc.filter_by_storm_ids(pairs, storm_ids)
    exclusions["outside_requested_storm_ids"] = int(len(pairs) - len(cohort_rows))

    finite = cohort_rows[
        cohort_rows["mean2min_kt"].apply(lambda v: pd.notna(v) and v > 0)
        & cohort_rows["gust_2min_kt"].apply(lambda v: pd.notna(v) and v > 0)
    ]
    exclusions["non_finite_or_non_positive_mean_or_gust"] = int(len(cohort_rows) - len(finite))

    at_or_above_threshold = finite[finite["mean2min_kt"] >= MIN_MEAN_KT]
    exclusions[f"mean2min_kt_below_{MIN_MEAN_KT:g}"] = int(len(finite) - len(at_or_above_threshold))

    # Recompute the ratio from its two source columns rather than trusting the stored
    # gf_3s_2min, and check the two agree within rounding: a mismatch beyond that means
    # gf_3s_2min was not in fact derived from these two columns for that row.
    recomputed = at_or_above_threshold["gust_2min_kt"] / at_or_above_threshold["mean2min_kt"]
    drift = (recomputed - at_or_above_threshold["gf_3s_2min"]).abs()
    inconsistent = drift > MAX_RATIO_DRIFT
    if inconsistent.any():
        raise cc.DataValidationError(
            f"gf_3s_2min disagrees with gust_2min_kt/mean2min_kt by more than "
            f"{MAX_RATIO_DRIFT} for {int(inconsistent.sum())} row(s); not rounding noise"
        )
    consistent = at_or_above_threshold.assign(gf_3s_2min=recomputed)

    not_repaired = consistent[~cc.parse_bool_flag(consistent["field_shift_recovered"])]
    exclusions["parser_repaired_rows"] = int(len(consistent) - len(not_repaired))

    # Missing or out-of-range exposure is counted as an exclusion, not coerced or
    # dropped silently by a later NaN comparison.
    has_exposure = not_repaired[
        not_repaired["ocean_frac_0_10km"].apply(lambda v: pd.notna(v) and 0.0 <= v <= 1.0)
    ]
    exclusions["missing_or_out_of_range_ocean_fraction"] = int(len(not_repaired) - len(has_exposure))

    usable = has_exposure
    land = usable[usable["ocean_frac_0_10km"] < OCEAN_FETCH_FRACTION]
    ocean = usable[usable["ocean_frac_0_10km"] >= OCEAN_FETCH_FRACTION]

    if len(land) == 0:
        raise cc.DataValidationError(
            "empty land-fetch training sample for this storm_id set; refusing to "
            "substitute the full-data factor or a historical default"
        )

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
            "max_ratio_drift_tolerance": MAX_RATIO_DRIFT,
            "parser_repaired_rows_excluded": True,
            "windows_used": int(len(usable)),
            "stations": int(usable["station"].nunique()),
            "storms_selected": len(storm_ids),
            "storms_contributing_usable_observations": int(usable["storm_id"].nunique()),
        },
        "selected_storm_ids": sorted(storm_ids),
        "contributing_storm_ids": sorted(usable["storm_id"].unique()),
        "exclusion_counts": exclusions,
    }


def _storms_label(storm_ids: set[str], pairs: pd.DataFrame) -> str:
    """Describe the storms and stations selected, e.g. "11 hurricanes 2016-2024, 43 stations"."""
    rows = pairs[pairs["storm_id"].isin(storm_ids)]
    years = rows["storm_id"].str[-4:].astype(int)
    return f"{len(storm_ids)} hurricanes {years.min()}-{years.max()}, {rows['station'].nunique()} stations"


def build_model(
    pairs_path: Path = PAIRS,
    *,
    cohort: str = cc.PRIMARY_COHORT,
    exclude_storm_ids: tuple[str, ...] = (),
) -> dict:
    """Load, resolve the cohort, fit, and wrap with fixture-level provenance.

    `cohort` names a manifest-defined storm_id set (primary_2016_2024 by default,
    or legacy_2004_2005 / combined_2004_2024); `exclude_storm_ids` removes specific
    storms from it (used for a leave-one-storm-out fold).
    """
    pairs = pd.read_csv(pairs_path)
    storm_ids = cc.cohort_storm_ids(cohort) - set(exclude_storm_ids)
    fitted = fit(pairs, storm_ids=storm_ids)
    return {
        "gust_factor_model_id": GUST_FACTOR_MODEL_ID,
        "evidence_status": "sourced",
        "evidence_note": (
            "Median measured ratio, reproducible from the committed observations. "
            "'Sourced' describes where the number comes from, not that its measurement "
            "basis (gust duration, sensor type) has been independently verified - see "
            "policy.note."
        ),
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
        "cohort_id": cohort,
        "excluded_storm_ids": sorted(exclude_storm_ids),
        "policy": cc.build_selection_policy(),
        "source": {
            "dataset": "Florida hurricane ASOS gust data (data/calibration/fl_hurricane_gust_data)",
            "file": pairs_path.name,
            "input_sha256": cc.sha256_of_file(pairs_path),
            "observations": "NCEI ASOS one-minute data via the Iowa Environmental Mesonet",
            "storms": _storms_label(storm_ids, pairs),
            "pairing": "NOAA/AOML ASOS gust method: peak gust over the same two-minute window as the mean",
        },
        "fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "backend/scripts/fit_gust_factor.py",
        **fitted,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pairs", nargs="?", default=str(PAIRS))
    parser.add_argument("--cohort", default=cc.PRIMARY_COHORT, choices=cc.COHORT_IDS)
    parser.add_argument("--fixture", default=str(FIXTURE))
    args = parser.parse_args(argv)

    model = build_model(Path(args.pairs), cohort=args.cohort)
    Path(args.fixture).write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")

    sel = model["selection"]
    print(f"cohort {model['cohort_id']}: {sel['windows_used']} windows >= {MIN_MEAN_KT:g} kt from "
          f"{sel['stations']} stations, {sel['storms_contributing_usable_observations']}/"
          f"{sel['storms_selected']} storms contributing")
    print(f"gust factor {model['gust_factor']} (land fetch, n={model['land_fetch']['n']}, p10-p90 {model['land_fetch']['p10']}-{model['land_fetch']['p90']})")
    print(f"ocean fetch {model['ocean_fetch']['median']} (n={model['ocean_fetch']['n']})")
    for band, stats in model["by_band"].items():
        print(f"  {band:10s} land {stats['land_fetch']['median']} (n={stats['land_fetch']['n']})  ocean {stats['ocean_fetch']['median']} (n={stats['ocean_fetch']['n']})")
    print(f"exclusions: {model['exclusion_counts']}")
    print(f"wrote {args.fixture}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
