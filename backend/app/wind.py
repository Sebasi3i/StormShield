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

What is still ASSUMED, and published in `metadata()` with every run: the storm size
(radius of maximum wind, decay, taper and cutoff) is one demonstration set applied to
every storm, because the simulator does not publish a size per storm; and the gust
factor is wind_field's demonstration value, 1.25. Every exposure is labeled `assumed`.

`claims.py` does not change with the wind model, because it consumes `WindExposure`
and not a track. The metric stamped on every exposure must match the metric the curves
are defined on: it is checked at import against wind_field's own label for this
configuration, and `claims.damage_fraction` rejects a mismatch instead of converting.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd
import wind_field
from wind_field import WindFieldConfig, compute_property_exposure, default_storm_parameters
from wind_field.schema import (
    DEFAULT_MAX_SEGMENT_DISTANCE_FRACTION_RMW,
    DEFAULT_MAX_TIME_STEP_MINUTES,
    DEMO_GUST_DURATION_SECONDS,
    DEMO_GUST_FACTOR,
    DEMO_STORM_PARAMETERS,
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

# ASSUMED. Ratio of a 3-second gust to the one-minute sustained wind the simulator
# reports, over open terrain: wind_field's demonstration value, agreed with the
# simulator owner for the platform. Not calibrated.
GUST_FACTOR = DEMO_GUST_FACTOR
GUST_DURATION_SECONDS = DEMO_GUST_DURATION_SECONDS

# Reported exposures below this are floored to zero. Well under the 75 mph where the
# curves first show damage, so this changes no payout; it keeps a light breeze far from
# the track from being published as a wind estimate.
NEGLIGIBLE_GUST_MPH = 20.0


def _config(catalog_id: str = "storm_catalog") -> WindFieldConfig:
    return WindFieldConfig(
        run_id="weather-risk-platform",
        catalog_id=catalog_id,
        gust_factor=GUST_FACTOR,
        gust_duration_seconds=GUST_DURATION_SECONDS,
        assumption_notes="Demonstration storm size applied to every storm; gust factor 1.25.",
    )


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
    storm: dict, properties: list[tuple[str, float, float]]
) -> tuple[list[WindExposure], list[dict]]:
    """One exposure row per property, including explicit zeros for homes missed.

    `properties` is (property_id, latitude, longitude). Returns the exposure rows and,
    alongside them, the closest-approach detail for each - which is what makes a zero
    row auditable: "0 mph, centre passed 480 km away" is a finding, "no row" is a bug.

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
            default_storm_parameters([storm["storm_id"]]),
            _config(),
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
        # Rounded to 0.01 mph so results are identical across platforms whose maths
        # libraries differ in the last bit; far below any meaningful difference.
        gust = round(float(row["peak_gust_mph"]), 2)
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


def metadata() -> dict:
    """Provenance for the wind step, published with every run that uses it."""
    config = _config()
    return {
        "wind_metric": WIND_METRIC,
        "evidence_status": "assumed",
        "model": "wind_field",
        "model_version": WIND_MODEL_VERSION,
        "package_version": wind_field.__version__,
        "gust_factor": GUST_FACTOR,
        "gust_duration_seconds": GUST_DURATION_SECONDS,
        "gust_factor_note": (
            "ASSUMED ratio of a 3-second gust to the simulator's one-minute sustained "
            "wind, over open terrain: wind_field's demonstration value, agreed for the "
            "platform. Not calibrated."
        ),
        "kt_to_mph": KT_TO_MPH,
        "storm_parameters": {
            key: DEMO_STORM_PARAMETERS[key]
            for key in (
                "rmw_km",
                "outer_decay_exponent",
                "taper_start_km",
                "cutoff_km",
                "parameter_status",
            )
        },
        "storm_parameters_note": (
            "ASSUMED storm size, identical for every storm, because the simulator does "
            "not publish a radius of maximum wind or size per storm. "
            + DEMO_STORM_PARAMETERS["source_note"]
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
            "multiplied by the gust factor. Gaps in a stored track are not bridged."
        ),
        "not_modeled": (
            "Forward-motion asymmetry, terrain roughness, and any weakening over land "
            "beyond what the track's max_wind_kt already carries."
        ),
    }
