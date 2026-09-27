"""Shared helpers for the ASOS calibration pipeline: cohort membership, explicit
input validation, and metrics on an explicit modeled/observed pair.

This is deliberately small and pipeline-specific, not a general framework: it holds
only what fit_gust_factor.py, calibrate_wind_field.py, validate_wind_field.py and
compare_calibration_cohorts.py would otherwise each reimplement slightly differently.
Every fitting decision (which candidates, which objective, which selection rule)
stays in its own script; this module loads data and checks it, and computes the same
handful of numbers the same way everywhere.

Cohorts, by storm_id (never by storm name, which is not guaranteed unique across
seasons and is easy to mistype):

  - primary_2016_2024: the 11 storms in storms_2016_2024.csv. The production cohort.
  - legacy_2004_2005: the 8 storms in storms_2004_2005.csv. Evaluated separately with
    the primary model frozen, and used in the combined/sensitivity comparison; never
    silently merged into "the" fit.
  - combined_2004_2024: the union of both, 19 storms. An explicit alternative, not a
    default.

A cohort's membership comes only from its manifest file. Nothing here infers cohort
from a storm_id's year: the manifest is authoritative, and asking to filter by an id
that manifest does not contain is an error, not a request to guess by year.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

DATASET = Path(__file__).resolve().parents[2] / "data" / "calibration" / "fl_hurricane_gust_data"

PRIMARY_COHORT = "primary_2016_2024"
LEGACY_COHORT = "legacy_2004_2005"
COMBINED_COHORT = "combined_2004_2024"

_MANIFESTS = {
    PRIMARY_COHORT: DATASET / "storms_2016_2024.csv",
    LEGACY_COHORT: DATASET / "storms_2004_2005.csv",
}
COHORT_IDS = (PRIMARY_COHORT, LEGACY_COHORT, COMBINED_COHORT)


class CohortError(ValueError):
    """A cohort id, a manifest, or a requested storm_id set failed validation."""


class DataValidationError(ValueError):
    """A required numeric or key-uniqueness invariant did not hold."""


# --------------------------------------------------------------------------- #
# Cohort membership
# --------------------------------------------------------------------------- #


def load_manifest(cohort: str) -> pd.DataFrame:
    """One of the two authoritative manifest files, not the union cohort."""
    if cohort not in _MANIFESTS:
        raise CohortError(f"no manifest for {cohort!r}; manifests exist for {sorted(_MANIFESTS)}")
    manifest = pd.read_csv(_MANIFESTS[cohort])
    if manifest["storm_id"].duplicated().any():
        dupes = sorted(manifest.loc[manifest["storm_id"].duplicated(), "storm_id"].unique())
        raise CohortError(f"{cohort} manifest has duplicate storm_id values: {dupes}")
    return manifest


def cohort_storm_ids(cohort: str) -> set[str]:
    """The storm_id set for a cohort, including the combined_2004_2024 union.

    Raises CohortError for any other name - this is the one place a typo'd cohort
    id is caught, rather than silently returning an empty or partial set.
    """
    if cohort == COMBINED_COHORT:
        return cohort_storm_ids(PRIMARY_COHORT) | cohort_storm_ids(LEGACY_COHORT)
    return set(load_manifest(cohort)["storm_id"])


def validate_cohorts_partition(all_storm_ids: Iterable[str]) -> None:
    """The two manifests must be disjoint and their union must exactly equal
    `all_storm_ids` (typically every storm_id in the committed pairs file).

    Disjointness means no storm is double-counted between "production" and "legacy
    sensitivity"; exact coverage means no storm is silently excluded from both, and no
    manifest lists a storm the observation data does not have.
    """
    primary = cohort_storm_ids(PRIMARY_COHORT)
    legacy = cohort_storm_ids(LEGACY_COHORT)
    overlap = primary & legacy
    if overlap:
        raise CohortError(f"storms in both cohort manifests: {sorted(overlap)}")
    all_ids = set(all_storm_ids)
    union = primary | legacy
    missing_from_manifests = all_ids - union
    unknown_to_data = union - all_ids
    if missing_from_manifests or unknown_to_data:
        raise CohortError(
            "cohort manifests do not exactly partition the observation data: "
            f"missing from manifests {sorted(missing_from_manifests)}, "
            f"in a manifest but not in the data {sorted(unknown_to_data)}"
        )


def filter_by_storm_ids(
    frame: pd.DataFrame,
    storm_ids: set[str],
    *,
    column: str = "storm_id",
    exclude: Iterable[str] = (),
) -> pd.DataFrame:
    """Rows whose `column` is in `storm_ids` and not in `exclude`.

    Every id in `storm_ids` and `exclude` must actually appear in `frame[column]` at
    least once (except ids being excluded, which by construction are removed and so
    only need to have existed before exclusion) - an id that matches nothing is far
    more likely to be a typo or a stale manifest than a deliberate no-op, so this
    raises rather than silently returning fewer rows than expected.
    """
    exclude = set(exclude)
    present = set(frame[column])
    unknown = (storm_ids | exclude) - present
    if unknown:
        raise CohortError(
            f"storm_id(s) not present in this data's {column!r} column: {sorted(unknown)}. "
            "Fail rather than silently include or exclude by assumption."
        )
    keep = (storm_ids - exclude)
    return frame[frame[column].isin(keep)]


def cohort_frame(
    frame: pd.DataFrame,
    cohort: str,
    *,
    column: str = "storm_id",
    exclude_storm_ids: Iterable[str] = (),
) -> pd.DataFrame:
    """`filter_by_storm_ids` for a named cohort rather than an explicit id set."""
    return filter_by_storm_ids(frame, cohort_storm_ids(cohort), column=column, exclude=exclude_storm_ids)


# --------------------------------------------------------------------------- #
# Explicit validation - no implicit truthiness, no silent NaN
# --------------------------------------------------------------------------- #

_TRUE_STRINGS = {"true", "1", "yes", "t", "y"}
_FALSE_STRINGS = {"false", "0", "no", "f", "n"}


def parse_bool_flag(series: pd.Series) -> pd.Series:
    """A boolean column, parsed explicitly rather than through Python truthiness.

    A real bool dtype (or a column of actual True/False objects) passes through
    unchanged. A string column is matched case-insensitively against a small
    recognised vocabulary; anything else - including a nonempty string like "False",
    which `bool("False")` would silently treat as true - raises DataValidationError
    naming the offending values, rather than being coerced.
    """
    if series.dtype == bool:
        return series
    if pd.api.types.is_object_dtype(series) and series.map(lambda v: isinstance(v, (bool, np.bool_))).all():
        return series.astype(bool)

    def parse_one(value: object) -> bool:
        text = str(value).strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
        raise DataValidationError(f"not a recognised boolean flag: {value!r}")

    return series.map(parse_one)


def require_finite(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    """Every named column must be entirely finite (no NaN, no +/-inf).

    Raises with the column name and how many rows failed, so a bad batch is a message
    naming the field to check, not a downstream NaN propagating silently into a
    median that looks plausible.
    """
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        bad = (~np.isfinite(values)).sum()
        if bad:
            raise DataValidationError(f"{column!r} has {bad} non-finite value(s) out of {len(frame)}")


def require_unique_keys(frame: pd.DataFrame, columns: list[str]) -> None:
    """No duplicate (columns...) combination - the key a caller is about to index by."""
    duplicated = frame.duplicated(subset=columns, keep=False)
    if duplicated.any():
        examples = frame.loc[duplicated, columns].drop_duplicates().head(5).to_dict("records")
        raise DataValidationError(
            f"duplicate {columns} keys ({int(duplicated.sum())} rows affected); "
            f"first duplicated key(s): {examples}"
        )


# --------------------------------------------------------------------------- #
# Metrics on an explicit modeled/observed pair
# --------------------------------------------------------------------------- #


def metrics_from_columns(
    frame: pd.DataFrame,
    modeled_col: str = "modeled_gust_kt",
    observed_col: str = "observed_gust_kt",
) -> dict:
    """Core, unrounded metrics comparing two explicit columns.

    Every script in this pipeline that compares a modeled gust with an observed one
    calls this rather than recomputing bias/MAE/ratio its own way, so "mean absolute
    error" means the same arithmetic everywhere. Deliberately unrounded - round only
    when a value is about to be written out or printed, never before a later
    calculation, so an exported figure never differs from the one actually used to
    pick a candidate.

    n=0 returns every other field as None rather than NaN or a fabricated zero, so a
    truly empty comparison (a fold with no strong-wind pairs, say) is visibly
    unevaluable rather than silently scoring as a perfect or a zero match.
    """
    if len(frame) == 0:
        return {
            "n": 0,
            "bias_kt": None,
            "mean_absolute_error_kt": None,
            "median_ratio": None,
            "within_15_percent": None,
            "observed_mean_kt": None,
            "modeled_mean_kt": None,
        }
    modeled = frame[modeled_col].astype(float)
    observed = frame[observed_col].astype(float)
    error = modeled - observed
    ratio = modeled / observed
    return {
        "n": int(len(frame)),
        "bias_kt": float(error.mean()),
        "mean_absolute_error_kt": float(error.abs().mean()),
        "median_ratio": float(ratio.median()),
        "within_15_percent": float(((ratio - 1).abs() <= 0.15).mean()),
        "observed_mean_kt": float(observed.mean()),
        "modeled_mean_kt": float(modeled.mean()),
    }


# --------------------------------------------------------------------------- #
# Provenance: hashes and selection/policy metadata
# --------------------------------------------------------------------------- #


def sha256_of_file(path: Path) -> str:
    """Hex digest of a file's bytes, so a fixture can name exactly which input
    version it was built from without embedding the input itself."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Measurement-policy facts that are true of every ASOS-derived fit in this pipeline,
# regardless of cohort. Recorded verbatim in every fixture that fits from these
# observations, so no fixture can drift from stating this honestly.
MEASUREMENT_POLICY = {
    "observation_target": "reported_asos_peak_gust",
    "mean_averaging_duration_seconds": 120,
    "paired_gust_search_window_seconds": 120,
    "observed_gust_duration_seconds": None,  # unknown/unverified - see note
    "sensor_metadata": "unavailable",
    "cohort_selection_basis": "temporal proxy (reporting era), not proof of sensor type",
    "model_output_duration_seconds": 3,
    "model_output_duration_basis": "assumed target convention, not verified against observation duration",
    "averaging_period_conversion_applied": False,
    "note": (
        "Observations are paired as a two-minute mean with the peak gust in the same "
        "window (column gf_3s_2min; the name is a legacy label and does not by itself "
        "establish the gust duration - see averaging_period_conversion_applied). "
        "Sensor type, verified gust duration, and anemometer height are not available "
        "for these stations. Cohort (reporting era) is used only as a data-availability "
        "and sensitivity grouping, never as a stand-in for a verified sensor "
        "correction, and no duration or height value here is inferred from it."
    ),
}


def build_selection_policy(**extra: object) -> dict:
    """MEASUREMENT_POLICY plus whatever fields a script wants to add or override.

    A thin wrapper rather than a bare dict literal so every call site is grepable and
    every fixture's policy block is guaranteed to include the shared facts above.
    """
    return {**MEASUREMENT_POLICY, **extra}
