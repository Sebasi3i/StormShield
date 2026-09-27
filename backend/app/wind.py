"""Wind field adapter: storm centre track -> peak gust at each property.

The hurricane simulator is a pure hazard generator: it hands over only the storm
CENTRE - `storm_id, timestamp, latitude, longitude, max_wind_kt`, one-minute sustained
wind in knots - and computes no property-level wind, damage or claims. The damage engine
in `claims.py` needs the opposite: a peak gust AT a property. This module bridges the
two with `wind_field`, the simulator owner's property-level wind model (a vendored
wheel, pinned in requirements.txt):

  - a radial wind profile around the centre: calm in the eye, rising to the storm's
    max_wind_kt at the radius of maximum wind, decaying beyond it and tapering to zero
    at a cutoff distance,
  - the centre moved along great circles in steps of at most 15 minutes and a tenth of
    the radius of maximum wind, so a property sees the storm's closest pass rather than
    only the nearest 6-hourly fix,
  - a gust factor, converting one-minute sustained wind to a 3-second gust,
  - a knots-to-mph conversion, the only exact step.

Storm size (radius of maximum wind, decay, taper and cutoff) comes from the storm-size
model in `fixtures/storm_size_model.json`, fitted by `scripts/fit_storm_size.py` to the
wind radii NOAA records in HURDAT2: a smaller eye for a stronger, lower-latitude storm.
The simulator publishes no size per storm, so the model is evaluated once per storm at
its peak intensity near Florida and held constant through the event. The gust factor
comes from `fixtures/gust_factor_model.json`, measured at Florida ASOS stations during
19 hurricanes (`scripts/fit_gust_factor.py`). The profile's outer decay exponent and an
open-terrain land exposure factor come from `fixtures/wind_calibration.json`, fitted
jointly to the peak gusts those stations recorded (`scripts/calibrate_wind_field.py`),
and `fixtures/wind_validation.json` records how the whole step then compares with the
observations (`scripts/validate_wind_field.py`). All of it is published in `metadata()`
with every run.

`claims.py` does not change with the wind model, because it consumes `WindExposure`
and not a track. The metric stamped on every exposure must match the metric the curves
are defined on: it is checked at import against wind_field's own label for this
configuration, and `claims.damage_fraction` rejects a mismatch instead of converting.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import pandas as pd
import wind_field
from wind_field import WindFieldConfig, compute_property_exposure
from wind_field.schema import (
    DEFAULT_MAX_SEGMENT_DISTANCE_FRACTION_RMW,
    DEFAULT_MAX_TIME_STEP_MINUTES,
    DEMO_GUST_DURATION_SECONDS,
    EXPECTED_TRACK_STEP_HOURS,
    KT_TO_MPH,
    WIND_MODEL_VERSION,
)

from .claims import WindExposure

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The metric this module produces, and the one the damage curves are defined on. The
# averaging duration (3 seconds), the reference height (10 m) and the terrain exposure
# (open) are all part of the name, because a curve read against a different convention
# is a different model.
WIND_METRIC = "peak_3s_gust_10m_open_terrain_mph"


@lru_cache
def load_gust_factor_model(path: str | None = None) -> dict:
    """The measured gust factor, with its provenance. See scripts/fit_gust_factor.py."""
    model_path = Path(path) if path else FIXTURES / "gust_factor_model.json"
    return json.loads(model_path.read_text(encoding="utf-8"))


@lru_cache
def load_wind_calibration(path: str | None = None) -> dict:
    """Decay exponent and land exposure factor fitted to station peak gusts.

    See scripts/calibrate_wind_field.py. Loaded on demand so the calibration script
    itself can run before the fixture exists.
    """
    model_path = Path(path) if path else FIXTURES / "wind_calibration.json"
    return json.loads(model_path.read_text(encoding="utf-8"))


# Ratio of a 3-second gust to the sustained wind, over open terrain: the median
# measured at land-fetch Florida ASOS stations in winds of 34 kt or more. wind_field's
# demonstration value (1.25) is no longer used.
GUST_FACTOR = float(load_gust_factor_model()["gust_factor"])
GUST_DURATION_SECONDS = DEMO_GUST_DURATION_SECONDS


def land_exposure_factor(bundle: dict | None = None) -> float:
    """Open-terrain land reduction applied to the marine profile's sustained wind.

    Every priced property is on land; the profile is a marine one with no surface
    friction. Calibrated jointly with the decay exponent against station peak gusts.

    `bundle`, when given, overrides the production fixture with an explicit
    `{gust_factor, land_exposure_factor, outer_decay_exponent, wind_calibration_id}`
    (see `_config` and `storm_parameters`) - for scoring an alternative jointly-fitted
    combination (a cohort's own fit, a leave-one-storm-out fold) without mutating this
    module's globals, its lru_cache state, or any fixture file.
    """
    if bundle is not None:
        return float(bundle["land_exposure_factor"])
    return float(load_wind_calibration()["land_exposure_factor"])


# The window within which a storm's size is taken: its most intense fix here, if any,
# sizes the whole event. Generous around Florida so an approaching storm is sized from
# its landfall-relevant state rather than a peak far out in the Atlantic.
FLORIDA_WINDOW = {"min_lat": 22.5, "max_lat": 32.5, "min_lon": -89.5, "max_lon": -78.0}

# Reported exposures below this are floored to zero. Well under the 75 mph where the
# curves first show damage, so this changes no payout; it keeps a light breeze far from
# the track from being published as a wind estimate.
NEGLIGIBLE_GUST_MPH = 20.0


def _config(catalog_id: str = "storm_catalog", bundle: dict | None = None) -> WindFieldConfig:
    """wind_field's own config: run/catalog identity, gust factor, gust duration.

    `bundle`, when given, overrides `gust_factor` (see `land_exposure_factor` for the
    full override shape); anything not present in this config - decay, land exposure -
    is applied downstream in `storm_parameters` and `exposures_for_storm`, not here.
    """
    gust_factor = float(bundle["gust_factor"]) if bundle is not None else GUST_FACTOR
    return WindFieldConfig(
        run_id="weather-risk-platform",
        catalog_id=catalog_id,
        gust_factor=gust_factor,
        gust_duration_seconds=GUST_DURATION_SECONDS,
        assumption_notes=(
            "Storm size per storm from the HURDAT2 wind-radii fit; gust factor "
            f"{gust_factor} measured at Florida ASOS stations; decay and land exposure "
            "calibrated to station peak gusts. See metadata() for the model ids."
        ),
    )


@lru_cache
def load_storm_size_model(path: str | None = None) -> dict:
    """The fitted storm-size model, with its provenance. See scripts/fit_storm_size.py."""
    model_path = Path(path) if path else FIXTURES / "storm_size_model.json"
    return json.loads(model_path.read_text(encoding="utf-8"))


def rmw_km(max_wind_kt: float, latitude: float, model: dict | None = None) -> float:
    """Radius of maximum wind for a storm of this intensity at this latitude, in km.

    ln(rmw) is linear in intensity and latitude, the fitted form; the result is clamped
    to the range the record spans for hurricanes so an extreme track cannot produce an
    implausible eye.
    """
    rmw = (model or load_storm_size_model())["rmw"]
    value = math.exp(
        rmw["intercept"] + rmw["per_kt"] * max_wind_kt + rmw["per_degree_latitude"] * latitude
    )
    low, high = rmw["bounds_km"]
    return min(high, max(low, value))


def size_basis_point(storm: dict) -> dict:
    """The track point a storm's size is taken from: its most intense fix inside the
    Florida window, or its overall peak if the track never enters it."""
    inside = [
        point
        for point in storm["track"]
        if FLORIDA_WINDOW["min_lat"] <= point["latitude"] <= FLORIDA_WINDOW["max_lat"]
        and FLORIDA_WINDOW["min_lon"] <= point["longitude"] <= FLORIDA_WINDOW["max_lon"]
    ]
    return max(inside or storm["track"], key=lambda point: point["max_wind_kt"])


def storm_parameters(storm: dict, model: dict | None = None, bundle: dict | None = None) -> dict:
    """This storm's size parameters: one row in wind_field's storm_parameters shape.

    The radius of maximum wind is the storm-size model at `size_basis_point`, constant
    through the event; the decay exponent is the station-calibrated value (or `bundle`'s,
    see `land_exposure_factor`); taper and cutoff are the record medians. `size_basis`
    records the point used so the value can be audited against the track.
    """
    model = model or load_storm_size_model()
    if bundle is not None:
        outer_decay_exponent = float(bundle["outer_decay_exponent"])
        calibration_id = bundle.get("wind_calibration_id", "bundle override")
    else:
        calibration = load_wind_calibration()
        outer_decay_exponent = calibration["outer_decay_exponent"]
        calibration_id = calibration["wind_calibration_id"]
    peak = size_basis_point(storm)
    return {
        "storm_id": storm["storm_id"],
        "rmw_km": round(rmw_km(peak["max_wind_kt"], peak["latitude"], model), 1),
        "outer_decay_exponent": outer_decay_exponent,
        "taper_start_km": model["taper"]["taper_start_km"],
        "cutoff_km": model["taper"]["cutoff_km"],
        "parameter_status": "sourced",
        "source_note": (
            f"{model['storm_size_model_id']}: RMW from the storm's peak near Florida of "
            f"{peak['max_wind_kt']:g} kt at {peak['latitude']:g} N; decay from "
            f"{calibration_id}; taper from record medians."
        ),
        "size_basis": {
            "peak_wind_kt": peak["max_wind_kt"],
            "latitude": peak["latitude"],
            "timestamp": peak["timestamp"],
        },
    }


# Fail at import, not at the first request, if wind_field's gust definition for this
# configuration ever stops matching the curves'.
if _config().wind_metric != WIND_METRIC:
    raise RuntimeError(
        f"wind_field produces {_config().wind_metric!r} but the damage curves are defined "
        f"on {WIND_METRIC!r}. Agree the gust definition rather than converting."
    )


def _track_segments(track: list[dict]) -> list[list[dict]]:
    """Split a stored track wherever consecutive points are not exactly 6 hours apart.

    wind_field refuses to interpolate across a gap, because it would have to invent the
    storm's position and strength inside it. A catalog that clipped a track to its map
    window can contain such a gap; each continuous stretch is modeled on its own instead.
    """
    segments: list[list[dict]] = []
    previous = None
    for point in track:
        timestamp = pd.Timestamp(point["timestamp"])
        if previous is None or timestamp - previous != pd.Timedelta(hours=EXPECTED_TRACK_STEP_HOURS):
            segments.append([])
        segments[-1].append(point)
        previous = timestamp
    return segments


def track_gaps(storm: dict) -> list[str]:
    """One warning per gap in a storm's stored track, for the response's warnings."""
    segments = _track_segments(storm["track"])
    warnings = []
    for before, after in zip(segments, segments[1:]):
        last, first = before[-1], after[0]
        hours = (pd.Timestamp(first["timestamp"]) - pd.Timestamp(last["timestamp"])) / pd.Timedelta(hours=1)
        warnings.append(
            f"{storm['storm_id']}: the stored track jumps {hours:g} hours, from step "
            f"{last['step']} to step {first['step']} (typically where the catalog import "
            "clipped it to its map window). Wind was modeled for each continuous stretch "
            "separately; nothing is assumed inside the gap, so any wind the storm produced "
            "there is not counted."
        )
    return warnings


def exposures_for_storm(
    storm: dict,
    properties: list[tuple[str, float, float]],
    bundle: dict | None = None,
) -> tuple[list[WindExposure], list[dict]]:
    """One exposure row per property, including explicit zeros for homes missed.

    `properties` is (property_id, latitude, longitude). Returns the exposure rows and,
    alongside them, the closest-approach detail for each - which is what makes a zero
    row auditable: "0 mph, centre passed 480 km away" is a finding, "no row" is a bug.

    `bundle`, when given, is an explicit
    `{gust_factor, land_exposure_factor, outer_decay_exponent, wind_calibration_id}`
    override (see `land_exposure_factor`) scoring this storm under a different
    jointly-fitted combination - a cohort's own fit, a leave-one-storm-out fold, a
    sensitivity comparison - than the production fixtures. It never mutates this
    module's globals, its lru_cache state, or any fixture file; omit it (the default)
    to score against the production bundle exactly as before.

    Raises ValueError, with wind_field's message, for inputs it cannot model (for
    example a property id that appears twice).
    """
    if not properties:
        return [], []

    property_table = pd.DataFrame(
        {
            "property_id": [property_id for property_id, _, _ in properties],
            "latitude": [latitude for _, latitude, _ in properties],
            "longitude": [longitude for _, _, longitude in properties],
        }
    )

    # One size for the whole event, from the storm-size model.
    parameters = storm_parameters(storm, bundle=bundle)
    parameter_table = pd.DataFrame([{k: v for k, v in parameters.items() if k != "size_basis"}])

    # Peak over every continuous stretch of the track, and the closest pass of any.
    peaks = None
    for segment in _track_segments(storm["track"]):
        track = pd.DataFrame(
            {
                "storm_id": storm["storm_id"],
                "timestamp": pd.to_datetime([point["timestamp"] for point in segment]),
                "latitude": [point["latitude"] for point in segment],
                "longitude": [point["longitude"] for point in segment],
                "max_wind_kt": [point["max_wind_kt"] for point in segment],
            }
        )
        result = compute_property_exposure(
            track,
            property_table,
            parameter_table,
            _config(bundle=bundle),
        )
        stretch = result.exposures.set_index("property_id")
        if peaks is None:
            peaks = stretch.copy()
        else:
            closest = pd.concat(
                [peaks["min_sampled_center_distance_km"], stretch["min_sampled_center_distance_km"]],
                axis=1,
            ).min(axis=1)
            stronger = stretch["peak_gust_mph"] > peaks["peak_gust_mph"]
            peaks.loc[stronger] = stretch.loc[stronger]
            peaks["min_sampled_center_distance_km"] = closest

    exposures: list[WindExposure] = []
    detail: list[dict] = []
    for property_id, _, _ in properties:
        row = peaks.loc[property_id]
        # The profile is marine; every property is on land. Rounded to 0.01 mph so
        # results are identical across platforms whose maths libraries differ in the
        # last bit; far below any meaningful difference.
        gust = round(float(row["peak_gust_mph"]) * land_exposure_factor(bundle), 2)
        if gust < NEGLIGIBLE_GUST_MPH:
            gust = 0.0
        exposures.append(
            WindExposure(
                storm_id=storm["storm_id"],
                property_id=property_id,
                peak_gust_mph=gust,
                wind_metric=WIND_METRIC,
            )
        )
        detail.append(
            {
                "storm_id": storm["storm_id"],
                "property_id": property_id,
                "peak_gust_mph": round(gust, 1),
                "peak_sustained_kt": round(float(row["peak_sustained_kt"]), 1),
                "peak_time_utc": (
                    pd.Timestamp(row["peak_gust_time_utc"]).round("min").isoformat()
                    if gust > 0
                    else None
                ),
                "closest_approach": {
                    "distance_km": round(float(row["min_sampled_center_distance_km"]), 1),
                },
                "rmw_km": parameters["rmw_km"],
                "land_exposure_factor": land_exposure_factor(bundle),
            }
        )

    return exposures, detail


@lru_cache
def load_catalog(path: str | None = None) -> dict:
    catalog_path = Path(path) if path else FIXTURES / "storm_catalog.json"
    return json.loads(catalog_path.read_text(encoding="utf-8"))


def reset_caches() -> None:
    load_catalog.cache_clear()


def storm_by_id(storm_id: str, catalog: dict | None = None) -> dict | None:
    catalog = catalog or load_catalog()
    return next((s for s in catalog["storms"] if s["storm_id"] == storm_id), None)


def _validation_summary() -> dict:
    """The headline numbers of fixtures/wind_validation.json, if it has been produced.

    scripts/validate_wind_field.py separates fitted (in-sample), holdout
    (out-of-sample, refit per fold), legacy-frozen (scored on storms the bundle never
    saw) and an optional combined-cohort sensitivity result rather than publishing one
    pooled figure - this keeps that split rather than collapsing it back into a single
    number, and generates every count from the fixture itself rather than repeating a
    hardcoded storm count in prose here.

    The flat top-level keys (storms, mean_absolute_error_kt, ...) are kept, equal to
    the fitted section, only so a caller reading metadata()["validation"] the way this
    function published it before the fitted/holdout/legacy split existed keeps working
    unchanged; "fitted" is the section to read going forward.
    """
    path = FIXTURES / "wind_validation.json"
    if not path.exists():
        return {"status": "not run"}
    report = json.loads(path.read_text(encoding="utf-8"))
    fitted = report["primary_fitted"]
    holdout = report["primary_holdout"]
    legacy = report["legacy_frozen_primary"]

    fitted_summary = {
        "validation_type": fitted["validation_type"],
        "storms": fitted["selection"]["storms"],
        "station_storm_pairs": fitted["selection"]["station_storm_pairs"],
        "median_ratio_modeled_over_observed": fitted["all"]["median_ratio_modeled_over_observed"],
        "mean_absolute_error_kt": fitted["all"]["mean_absolute_error_kt"],
        "within_15_percent": fitted["all"]["within_15_percent"],
        "within_75_km_median_ratio": fitted["within_75_km"]["median_ratio_modeled_over_observed"],
        "beyond_75_km_median_ratio": fitted["beyond_75_km"]["median_ratio_modeled_over_observed"],
        "observed_64kt_or_more_median_ratio": fitted["observed_64kt_or_more"]["median_ratio_modeled_over_observed"],
    }
    summary = {
        "wind_validation_id": report["wind_validation_id"],
        "fitted": fitted_summary,
        "holdout": {
            "validation_type": holdout["validation_type"],
            "cohort_id": holdout["cohort_id"],
            "out_of_sample_mean_absolute_error_kt": holdout["out_of_sample"]["mean_absolute_error_kt"],
            "out_of_sample_median_ratio": holdout["out_of_sample"]["median_ratio"],
        },
        "legacy_frozen_primary": {
            "validation_type": legacy["validation_type"],
            "storms": legacy["selection"]["storms"],
            "station_storm_pairs": legacy["selection"]["station_storm_pairs"],
            "median_ratio_modeled_over_observed": legacy["all"]["median_ratio_modeled_over_observed"],
            "mean_absolute_error_kt": legacy["all"]["mean_absolute_error_kt"],
        },
        **fitted_summary,
    }
    if "combined_sensitivity" in report:
        combined = report["combined_sensitivity"]
        summary["combined_sensitivity"] = {
            "validation_type": combined["validation_type"],
            "wind_calibration_id": combined["wind_calibration_id"],
            "median_ratio_modeled_over_observed": combined["all"]["median_ratio_modeled_over_observed"],
            "mean_absolute_error_kt": combined["all"]["mean_absolute_error_kt"],
        }
    return summary


def metadata() -> dict:
    """Provenance for the wind step, published with every run that uses it."""
    config = _config()
    model = load_storm_size_model()
    gust_model = load_gust_factor_model()
    calibration = load_wind_calibration()
    validation = _validation_summary()
    return {
        "wind_metric": WIND_METRIC,
        "evidence_status": "sourced",
        "evidence_note": (
            "Storm size, gust factor, decay and land exposure each have a documented "
            "source and a fit; the step as a whole is validated against station peak "
            "gusts from 11 Florida hurricanes (see validation). Sourced does not mean "
            "the residual errors are small: see validation.mean_absolute_error_kt."
        ),
        "model": "wind_field",
        "model_version": WIND_MODEL_VERSION,
        "package_version": wind_field.__version__,
        "gust_factor": GUST_FACTOR,
        "gust_duration_seconds": GUST_DURATION_SECONDS,
        "gust_factor_note": (
            f"{gust_model['gust_factor_model_id']}: {gust_model['gust_factor_basis']}, "
            f"n={gust_model['land_fetch']['n']} windows, 10th-90th percentile "
            f"{gust_model['land_fetch']['p10']}-{gust_model['land_fetch']['p90']}. "
            + gust_model["sustained_basis_note"]
        ),
        "land_exposure_factor": calibration["land_exposure_factor"],
        "calibration": {
            "wind_calibration_id": calibration["wind_calibration_id"],
            "outer_decay_exponent": calibration["outer_decay_exponent"],
            "land_exposure_factor": calibration["land_exposure_factor"],
            "objective": calibration["objective"],
            "station_storm_pairs": calibration["data"]["station_storm_pairs"],
            "in_sample_mean_absolute_error_kt": calibration["chosen"]["mean_absolute_error_kt"],
            "out_of_sample_mean_absolute_error_kt": (
                calibration["cross_validation"]["out_of_sample"]["mean_absolute_error_kt"]
            ),
            "out_of_sample_method": calibration["cross_validation"]["method"],
            "interpretation": calibration["interpretation"],
        },
        "validation": validation,
        "kt_to_mph": KT_TO_MPH,
        "storm_parameters": {
            "parameter_status": "sourced",
            "storm_size_model_id": model["storm_size_model_id"],
            "rmw_form": model["rmw"]["form"],
            "rmw_bounds_km": model["rmw"]["bounds_km"],
            "outer_decay_exponent": model["outer_decay_exponent"]["value"],
            "taper_start_km": model["taper"]["taper_start_km"],
            "cutoff_km": model["taper"]["cutoff_km"],
            "fit": {
                "dataset": model["source"]["dataset"],
                "file": model["source"]["file"],
                "rmw_fixes": model["rmw"]["fixes"],
                "rmw_storms": model["rmw"]["storms"],
                "rmw_seasons": model["rmw"]["seasons"],
                "rmw_r_squared": model["rmw"]["r_squared"],
                "decay_fixes": model["outer_decay_exponent"]["fixes"],
                "taper_fixes": model["taper"]["fixes"],
            },
            "validation_status": "validated jointly with the gust factor and decay; see validation",
        },
        "storm_parameters_note": (
            "Storm size per storm from the storm-size model fitted to HURDAT2 wind "
            "radii: the radius of maximum wind from the storm's peak intensity and "
            "latitude, decay and taper from record-wide medians, constant within each "
            "event. The per-storm values used are in metadata.storm_size. "
            + model["scope"]
        ),
        "track_interpolation": {
            "max_time_step_minutes": DEFAULT_MAX_TIME_STEP_MINUTES,
            "max_segment_distance_fraction_rmw": DEFAULT_MAX_SEGMENT_DISTANCE_FRACTION_RMW,
        },
        "reference_height_m": config.reference_height_m,
        "terrain_exposure": "open",
        "negligible_gust_floor_mph": NEGLIGIBLE_GUST_MPH,
        "method": (
            "For each property, the peak over the whole track of: the storm centre moved "
            "along great circles in steps of at most max_time_step_minutes and "
            "max_segment_distance_fraction_rmw x rmw_km; at each step, sustained wind from "
            "a modified-Rankine profile (max_wind_kt x r/rmw inside the radius of maximum "
            "wind, max_wind_kt x (rmw/r)^outer_decay_exponent outside it, cosine-tapered "
            "to zero between taper_start_km and cutoff_km), converted to mph and "
            "multiplied by the land exposure factor and the gust factor. Gaps in a "
            "stored track are not bridged."
        ),
        "not_modeled": (
            "Forward-motion asymmetry, local terrain roughness beyond the single "
            "open-terrain land factor, and any weakening over land beyond what the "
            "track's max_wind_kt already carries."
        ),
    }
