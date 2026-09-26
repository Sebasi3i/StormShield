"""Wind field adapter: storm centre track -> peak gust at each property.

This module exists because of a gap between two other modules. The hurricane simulator
is a pure hazard generator: its README is explicit that it hands the next module only
`storm_id, timestamp, latitude, longitude, max_wind_kt` - one-minute sustained wind at
the storm CENTRE - and that it "does not compute property-level wind, damage, or
claims". The damage engine in `claims.py` needs the opposite: a peak gust AT a property.

Everything here is the assumption that bridges them, and all of it is provisional:

  - a radial decay profile, standing in for a real parametric wind field,
  - a gust factor, standing in for an agreed gust averaging duration,
  - a knots-to-mph conversion, which is the only exact step.

That makes this the least defensible module in the backend, which is why it is its own
file with its own metadata block rather than three constants buried in an endpoint.
Replace it wholesale when Adryel's wind field model lands; `claims.py` does not change
when that happens, because it consumes `WindExposure` and not a track.

The metric name this module stamps on every exposure must match the metric the curves
are defined on. `claims.damage_fraction` rejects a mismatch instead of converting.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from . import risk
from .claims import WindExposure

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The metric this module produces. The averaging duration (3 seconds), the reference
# height (10 m) and the terrain exposure (open) are all part of the name, because a
# curve read against a different convention is a different model.
WIND_METRIC = "peak_3s_gust_10m_open_terrain_mph"

# Exact unit conversion, the one number here that is not an assumption.
KT_TO_MPH = 1.15078

# ASSUMED. Ratio of a 3-second gust to the one-minute sustained wind the simulator
# reports, over open terrain. Values around 1.2-1.4 are conventional for marine and
# open exposures; 1.30 sits mid-range. This must be confirmed with the simulator owner
# before any output is treated as a wind estimate rather than a demonstration.
GUST_FACTOR = 1.30

# ASSUMED. e-folding distance for wind decay away from the centre, reused from the
# existing simulation endpoint (risk.DEFAULT_WIND_DECAY_KM) so the platform carries one
# wind field assumption rather than two that disagree. A real model would use radius of
# maximum wind, a Holland B profile and asymmetry from forward motion; the simulator
# publishes none of those, so a single exponential is what the available data supports.
DECAY_KM = risk.DEFAULT_WIND_DECAY_KM

# Reported exposures below this are floored to zero. Well under the 75 mph where the
# curves first show damage, so this changes no payout; it keeps a 4 mph breeze 600 km
# away from being published as a wind estimate.
NEGLIGIBLE_GUST_MPH = 20.0


def gust_at_distance(centre_wind_kt: float, distance_km: float) -> float:
    """Peak gust in mph at `distance_km` from a centre with `centre_wind_kt` sustained."""
    sustained_kt = centre_wind_kt * math.exp(-distance_km / DECAY_KM)
    return sustained_kt * KT_TO_MPH * GUST_FACTOR


def peak_gust_mph(latitude: float, longitude: float, track: list[dict]) -> tuple[float, dict | None]:
    """Strongest gust this track ever puts on this point, and the step that produced it.

    The maximum over the whole track, not the value at landfall: a property 80 km up
    the coast from the landfall point may see its worst wind hours earlier, while the
    centre is still offshore.
    """
    worst_gust = 0.0
    worst_step: dict | None = None

    for point in track:
        distance = risk.haversine_km(
            latitude, longitude, point["latitude"], point["longitude"]
        )
        gust = gust_at_distance(point["max_wind_kt"], distance)
        if gust > worst_gust:
            worst_gust, worst_step = gust, {
                "step": point["step"],
                "timestamp": point["timestamp"],
                "centre_lat": point["latitude"],
                "centre_lon": point["longitude"],
                "centre_wind_kt": point["max_wind_kt"],
                "distance_km": round(distance, 1),
            }

    if worst_gust < NEGLIGIBLE_GUST_MPH:
        return 0.0, worst_step
    return worst_gust, worst_step


def exposures_for_storm(
    storm: dict, properties: list[tuple[str, float, float]]
) -> tuple[list[WindExposure], list[dict]]:
    """One exposure row per property, including explicit zeros for homes missed.

    `properties` is (property_id, latitude, longitude). Returns the exposure rows and,
    alongside them, the closest-approach detail for each - which is what makes a zero
    row auditable: "0 mph, centre passed 480 km away" is a finding, "no row" is a bug.
    """
    exposures: list[WindExposure] = []
    detail: list[dict] = []

    for property_id, latitude, longitude in properties:
        gust, step = peak_gust_mph(latitude, longitude, storm["track"])
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
                "closest_approach": step,
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
    return {
        "wind_metric": WIND_METRIC,
        "evidence_status": "assumed",
        "gust_factor": GUST_FACTOR,
        "gust_factor_note": (
            "ASSUMED ratio of 3-second gust to the simulator's one-minute sustained "
            "centre wind, over open terrain. Confirm with the simulator owner before "
            "treating any output as a wind estimate."
        ),
        "kt_to_mph": KT_TO_MPH,
        "decay_km": DECAY_KM,
        "decay_note": (
            "ASSUMED exponential decay of wind with distance from the centre, "
            "e-folding at this distance. Shared with the existing /api/simulate "
            "endpoint. No radius of maximum wind, pressure profile, forward-motion "
            "asymmetry or terrain roughness is modeled, because the simulator does not "
            "publish them."
        ),
        "reference_height_m": 10,
        "terrain_exposure": "open",
        "negligible_gust_floor_mph": NEGLIGIBLE_GUST_MPH,
        "method": (
            "For each property, the peak gust is the maximum over every track step of "
            "centre_wind_kt * exp(-distance_km / decay_km) * kt_to_mph * gust_factor."
        ),
        "replace_with": (
            "A parametric or numerical wind field from the simulator owner. This module "
            "is the seam: claims.py consumes WindExposure rows and is unaffected."
        ),
    }
