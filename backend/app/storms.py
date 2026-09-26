"""The single adapter between the vendored hurricane simulator and this backend.

Everything that knows the simulator exists lives here. `claims.py` never hears about
tracks, `wind.py` never hears about the simulator, and a re-vendor of his package
touches this file and nothing else.

One job: generate a hurricane that actually hits Florida.

The simulator is a pure hazard generator over the real HURDAT2 record, so it produces
the Atlantic as it is - and the Atlantic mostly misses Florida. Only about 13% of its
storms peak at Category 3 or above, and only a fraction of those bring hurricane-force
wind to the state, so roughly 1 draw in 50 qualifies. That is not a defect: measured the
same way, 174 years of real observations put just 8.2% of Atlantic storms over Florida at
hurricane strength. Real hurricanes miss Florida too.

So `generate_florida_storm` keeps drawing until one qualifies, which takes about a second.
It reports how many draws that took, because that count is the rarity of the event being
shown and the only honest replacement for a probability. Nothing here produces a rate, a
return period or an expected annual loss.

The storm is not steered at anybody's houses. A Panhandle hurricane is a real Florida
strike that legitimately does nothing to a Miami portfolio, and suppressing those would
turn an honest hazard model into a highlight reel.
"""

from __future__ import annotations

import random
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import risk

VENDOR = Path(__file__).resolve().parents[1] / "vendor"

HURDAT2 = VENDOR / "data" / "hurdat2-1851-2025-091226.txt"

#: Saffir-Simpson lower bounds in knots, matching the vendored package's own bins.
CATEGORY_MIN_KT = {1: 64.0, 2: 83.0, 3: 96.0, 4: 113.0, 5: 137.0}

#: Below Category 3 a direct hit rarely clears a 5% hurricane deductible, so the
#: generator does not bother with weaker storms by default.
DEFAULT_MIN_CATEGORY = 3

#: Storms generated per call to the simulator while searching. Batching matters: the
#: simulator refits its analog model on every call, so asking for 60 storms once costs
#: far less than asking for one storm 60 times.
DRAW_BATCH = 60

#: Give up after this many draws rather than spinning forever. At a ~2% qualifying rate
#: this is about a 1-in-10-million run of bad luck, so hitting it means something is
#: wrong with the criteria rather than with the dice.
MAX_DRAWS = 3000

#: Florida's bounding box, matching risk.FL_BOUNDS. A cheap pre-filter only - see
#: `strikes_florida` for why it is not sufficient alone.
FLORIDA = {"min_lat": 24.3, "max_lat": 31.1, "min_lon": -87.7, "max_lon": -79.9}

#: A storm must be at least this strong to count as striking Florida. Below hurricane
#: force a system crossing the coast is not the event an insurance model cares about;
#: this is the same threshold the vendored package uses for landfall.
FLORIDA_STRIKE_MIN_KT = 64.0

#: How close to a Florida county centroid a hurricane-force centre must pass to count as
#: reaching the state. Wide enough to keep a storm running parallel to the coast without
#: technically landfalling - often the costliest kind - and narrow enough to exclude one
#: crossing the middle of the Gulf.
FLORIDA_REACH_KM = 75.0

#: Track points beyond this box cannot raise wind at a Florida property: the wind field
#: decays with a 150 km e-folding, so at this ~600 km margin even a 150 kt storm
#: contributes under 10 mph. Trimming here keeps a response from carrying the whole
#: Atlantic, and cannot change a payout.
TRACK_WINDOW = {"min_lat": 18.0, "max_lat": 37.0, "min_lon": -95.0, "max_lon": -73.0}


class SimulatorUnavailable(RuntimeError):
    """The vendored simulator or its HURDAT2 record could not be loaded."""


class NoQualifyingStorm(RuntimeError):
    """MAX_DRAWS storms were generated and none met the criteria."""


def category_of(peak_wind_kt: float) -> int:
    """Saffir-Simpson category, or 0 for anything below hurricane strength."""
    for category in (5, 4, 3, 2, 1):
        if peak_wind_kt >= CATEGORY_MIN_KT[category]:
            return category
    return 0


# --------------------------------------------------------------------------- #
# The simulator
# --------------------------------------------------------------------------- #


def _simulator():
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    try:
        import hurricane_simulator
    except ImportError as error:  # pragma: no cover - depends on the vendored tree
        raise SimulatorUnavailable(
            f"could not import the vendored hurricane_simulator from {VENDOR}: {error}"
        ) from error
    return hurricane_simulator


@lru_cache
def historical_data() -> Any:
    """The HURDAT2 record his model bootstraps from. Loaded once, reused forever.

    Around 0.7 seconds and 55,524 observations, so it is warmed at application startup
    rather than paid for by whoever presses the button first.
    """
    if not HURDAT2.exists():
        raise SimulatorUnavailable(f"missing the HURDAT2 record at {HURDAT2}")
    return _simulator().load_hurdat2(str(HURDAT2))


def reset_caches() -> None:
    historical_data.cache_clear()


def simulator_info() -> dict:
    """What the generator is, and what it is not. Published alongside every scenario."""
    return {
        "package": "hurricane_simulator",
        "version": getattr(_simulator(), "__version__", "unknown"),
        "vendored_from": str(VENDOR),
        "historical_record": HURDAT2.name,
        "method": (
            "Monte Carlo synthetic tracks: genesis points are bootstrapped from real "
            "historical storm origins with jitter, then each 6-hour step adopts the "
            "realised position and intensity change of one of the 12 nearest historical "
            "analogs, k-d tree matched on latitude, longitude, wind and time of year. "
            "Over land, intensity relaxes toward a residual instead."
        ),
        "boundary": (
            "The simulator emits storm_id, timestamp, latitude, longitude and "
            "max_wind_kt only - one-minute sustained wind at the storm CENTRE. It "
            "computes no property-level wind, damage or claims, by design."
        ),
        "florida_strike_definition": (
            f"A hurricane-force centre ({FLORIDA_STRIKE_MIN_KT:.0f} kt or more) either "
            f"over Florida land, or within {FLORIDA_REACH_KM:.0f} km of a Florida county "
            "centroid. A bounding box is not used as the test on its own: Florida's box "
            "contains several hundred kilometres of open Gulf and Atlantic, so a storm "
            "crossing the eastern Gulf would qualify while sitting 340 km from the "
            "nearest building."
        ),
        "not_a_rate": (
            "Storms are generated until one qualifies, so a generated storm is NOT a "
            "typical storm and its loss is NOT an expected loss. Do not compute average "
            "annual loss, exceedance probabilities or return periods from these results. "
            "Annual event rates are the simulator owner's input."
        ),
    }


# --------------------------------------------------------------------------- #
# Does it hit Florida?
# --------------------------------------------------------------------------- #


def florida_strike_point(track: list[dict]) -> dict | None:
    """The first track point where hurricane-force wind reaches Florida.

    Distinct from the simulator's `landfall_*` columns, which record a storm's FIRST
    landfall anywhere - often Cuba, Hispaniola or the Bahamas on a track that goes on to
    cross Florida days later. A client that centres its map on `landfall_lat` will point
    at Haiti, so the Florida strike is reported separately.
    """
    margin_lat, margin_lon = 0.8, 0.9

    for point in track:
        if point["max_wind_kt"] < FLORIDA_STRIKE_MIN_KT:
            continue
        latitude, longitude = point["latitude"], point["longitude"]

        if not (
            FLORIDA["min_lat"] - margin_lat <= latitude <= FLORIDA["max_lat"] + margin_lat
            and FLORIDA["min_lon"] - margin_lon
            <= longitude
            <= FLORIDA["max_lon"] + margin_lon
        ):
            continue

        over_florida_land = point.get("is_over_land") and (
            FLORIDA["min_lat"] <= latitude <= FLORIDA["max_lat"]
            and FLORIDA["min_lon"] <= longitude <= FLORIDA["max_lon"]
        )
        nearest = min(
            (
                (risk.haversine_km(county.lat, county.lon, latitude, longitude), county)
                for county in risk.FL_COUNTIES
            ),
            key=lambda pair: pair[0],
        )
        if over_florida_land or nearest[0] <= FLORIDA_REACH_KM:
            return {
                "timestamp": point["timestamp"],
                "latitude": latitude,
                "longitude": longitude,
                "wind_kt": point["max_wind_kt"],
                "category": point["category"],
                "over_land": bool(point.get("is_over_land")),
                "nearest_county": nearest[1].name,
                "nearest_county_km": round(nearest[0], 1),
            }

    return None


def strikes_florida(track: list[dict]) -> bool:
    """Did hurricane-force wind actually reach Florida?

    A bounding box will not answer this. Florida's box contains several hundred
    kilometres of open Gulf and Atlantic, so a storm crossing the eastern Gulf passes a
    box test while sitting 340 km from the nearest house - which is exactly how an
    earlier version of this filter let landfalls in Yucatan, Cuba and Louisiana through.

    So the question is asked properly: was a hurricane-force centre over Florida land,
    or close enough to a Florida county to matter. The land flag comes from the
    simulator's own land mask and the county centroids are the ones the risk model
    already uses, so no new geography is invented here.

    The box survives as a cheap pre-filter, because the 67-county distance scan should
    only run for points already near the state.
    """
    margin_lat, margin_lon = 0.8, 0.9  # roughly FLORIDA_REACH_KM at these latitudes

    for point in track:
        if point["max_wind_kt"] < FLORIDA_STRIKE_MIN_KT:
            continue
        latitude, longitude = point["latitude"], point["longitude"]

        if not (
            FLORIDA["min_lat"] - margin_lat <= latitude <= FLORIDA["max_lat"] + margin_lat
            and FLORIDA["min_lon"] - margin_lon
            <= longitude
            <= FLORIDA["max_lon"] + margin_lon
        ):
            continue

        if point.get("is_over_land") and (
            FLORIDA["min_lat"] <= latitude <= FLORIDA["max_lat"]
            and FLORIDA["min_lon"] <= longitude <= FLORIDA["max_lon"]
        ):
            return True

        for county in risk.FL_COUNTIES:
            if (
                risk.haversine_km(county.lat, county.lon, latitude, longitude)
                <= FLORIDA_REACH_KM
            ):
                return True

    return False


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def _track_of(frame) -> list[dict]:
    """One storm's track rows, trimmed to the window that can affect Florida."""
    return [
        {
            "step": int(point.step),
            "timestamp": str(point.timestamp),
            "latitude": round(float(point.latitude), 4),
            "longitude": round(float(point.longitude), 4),
            "max_wind_kt": round(float(point.max_wind_kt), 1),
            "category": str(point.category),
            "is_over_land": bool(point.is_over_land),
        }
        for point in frame.itertuples()
        if TRACK_WINDOW["min_lat"] <= float(point.latitude) <= TRACK_WINDOW["max_lat"]
        and TRACK_WINDOW["min_lon"] <= float(point.longitude) <= TRACK_WINDOW["max_lon"]
    ]


def _optional(value: Any, digits: int = 4) -> float | None:
    # The simulator leaves landfall columns as NaN for storms that never made one.
    if value is None or value != value:
        return None
    return round(float(value), digits)


def generate_florida_storm(
    min_category: int = DEFAULT_MIN_CATEGORY,
    seed: int | None = None,
    batch: int = DRAW_BATCH,
    max_draws: int = MAX_DRAWS,
) -> tuple[dict, dict]:
    """Generate storms until one is `min_category`+ and strikes Florida.

    Returns the storm and a generation block. That block carries `storms_generated`,
    which is the whole honesty story: a storm found on the 90th draw is roughly a
    1-in-90 event, and without that number a reader cannot tell a routine storm from a
    rare one.

    A `seed` makes the run reproducible - the simulator guarantees identical output for
    identical inputs and seed - which is what lets the tests assert on specific numbers.
    """
    if min_category not in CATEGORY_MIN_KT:
        raise ValueError(f"min_category must be 1-5, got {min_category}")

    hs = _simulator()
    historical = historical_data()
    threshold = CATEGORY_MIN_KT[min_category]

    # Distinct per batch, so a seeded run stays reproducible while successive batches
    # do not simply repeat the same storms.
    batch_seeds = random.Random(seed) if seed is not None else None

    drawn = 0
    considered_major = 0

    while drawn < max_draws:
        batch_seed = batch_seeds.randrange(2**31) if batch_seeds is not None else None
        tracks, summary = hs.simulate_hurricanes(
            historical, num_storms=batch, seed=batch_seed
        )
        drawn += batch

        grouped = {storm_id: frame for storm_id, frame in tracks.groupby("storm_id")}
        majors = summary[summary["max_wind_kt"] >= threshold]

        for row in majors.itertuples():
            considered_major += 1
            track = _track_of(grouped[row.storm_id])
            strike = florida_strike_point(track)
            if strike is None:
                continue

            peak = round(float(row.max_wind_kt), 1)
            storm = {
                "storm_id": str(row.storm_id),
                "category": category_of(peak),
                "peak_wind_kt": peak,
                "genesis_time": str(row.genesis_time),
                "duration_hours": float(row.duration_hours),
                # Where it hits FLORIDA. This is the one a map should centre on.
                "florida_strike": strike,
                # The simulator's first landfall ANYWHERE - frequently Cuba, Hispaniola
                # or the Bahamas on a track that reaches Florida days later. Kept for
                # completeness; do not confuse it with florida_strike.
                "landfall": bool(row.landfall),
                "landfall_time": str(row.landfall_time) if row.landfall else None,
                "landfall_lat": _optional(row.landfall_lat),
                "landfall_lon": _optional(row.landfall_lon),
                "landfall_wind_kt": _optional(row.landfall_wind_kt, 1),
                "track": track,
            }
            generation = {
                "method": "generated until a qualifying storm appeared",
                "min_category": min_category,
                "storms_generated": drawn,
                "majors_considered": considered_major,
                "qualifying_rate": round(1 / drawn, 6),
                "seed": seed,
                "interpretation": (
                    f"{drawn:,} storms were generated before one reached Category "
                    f"{min_category} and struck Florida, so this is roughly a 1-in-"
                    f"{drawn:,} storm rather than a typical one. It was NOT selected for "
                    f"being damaging to any particular portfolio, so a result of little "
                    f"or no damage is a real outcome. No annual rate is applied and this "
                    f"is not an expected loss."
                ),
            }
            return storm, generation

    raise NoQualifyingStorm(
        f"generated {drawn:,} storms without finding a Category {min_category}+ storm "
        f"that strikes Florida. Expected about 1 in 50, so this points at the criteria "
        f"rather than bad luck."
    )
