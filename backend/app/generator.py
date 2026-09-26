"""Storm generation on request: the hurricane simulator, loaded on first use.

Importing `hurricane_simulator` loads a global land/sea map of about 890 MB, and fitting
its analog model to the NOAA record takes a few seconds more. Neither happens when the
API starts: the simulator is imported and fitted on the first generate request, once,
even if several requests arrive together. Until then the API stays as light as it was;
after that, generating a storm takes milliseconds.

Generated storms come back in the stored catalog's own shape, so the dashboard draws
them and `POST /api/v1/storm-losses` prices them (via its `storms` field) exactly like
catalog storms. The API keeps no state: the client holds the storms it generated.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone

from . import wind

# Upper bound on one request's ensemble. Each storm is also priced on demand, so this
# keeps a generate-then-price round trip to a couple of seconds.
MAX_STORMS_PER_REQUEST = 10

_lock = threading.Lock()
_generator = None


def storm_generator():
    """The fitted `hurricane_simulator.StormGenerator`, built on first use."""
    global _generator
    if _generator is None:
        with _lock:
            if _generator is None:
                import hurricane_simulator

                _generator = hurricane_simulator.StormGenerator.fit(
                    hurricane_simulator.load_bundled_hurdat2()
                )
    return _generator


def is_loaded() -> bool:
    return _generator is not None


def default_start_date() -> date:
    """10 September of the current year: close to the peak of the Atlantic season."""
    return date(datetime.now(timezone.utc).year, 9, 10)


def _timestamp(value) -> str | None:
    if value is None or value != value:  # None or NaT
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _number(value, digits: int) -> float | None:
    if value is None or value != value:  # None or NaN
        return None
    return round(float(value), digits)


def generate_catalog(
    latitude: float,
    longitude: float,
    max_wind_kt: float,
    start_date: date,
    seed: int,
    count: int,
) -> dict:
    """An ensemble of `count` storms from one starting point, as a storm catalog.

    Raises ValueError, with the simulator's reason, for a start it rejects (over land,
    or far from where Atlantic storms have formed).
    """
    import hurricane_simulator

    ensemble = storm_generator().generate_ensemble(
        latitude,
        longitude,
        max_wind_kt,
        start_date.isoformat(),
        seed,
        count,
        storm_id_prefix=f"G{seed}-",
    )

    storms = []
    for member_seed, summary in zip(ensemble.member_seeds, ensemble.summary.itertuples(index=False)):
        track = ensemble.tracks[ensemble.tracks["storm_id"] == summary.storm_id]
        storms.append(
            {
                "storm_id": summary.storm_id,
                "member_seed": member_seed,
                "genesis_time": _timestamp(summary.genesis_time),
                "peak_wind_kt": _number(summary.max_wind_kt, 1),
                "duration_hours": float(summary.duration_hours),
                "landfall": bool(summary.landfall),
                "landfall_time": _timestamp(summary.landfall_time),
                "landfall_lat": _number(summary.landfall_lat, 4),
                "landfall_lon": _number(summary.landfall_lon, 4),
                "landfall_wind_kt": _number(summary.landfall_wind_kt, 1),
                "track": [
                    {
                        "step": int(point.step),
                        "timestamp": _timestamp(point.timestamp),
                        "latitude": round(float(point.latitude), 4),
                        "longitude": round(float(point.longitude), 4),
                        "max_wind_kt": round(float(point.max_wind_kt), 1),
                        "category": str(point.category),
                        "is_over_land": bool(point.is_over_land),
                    }
                    for point in track.itertuples(index=False)
                ],
            }
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    start = {
        "latitude": latitude,
        "longitude": longitude,
        "max_wind_kt": max_wind_kt,
        "date": start_date.isoformat(),
    }
    return {
        "catalog_id": (
            f"generated-{latitude:.2f}_{longitude:.2f}-{max_wind_kt:g}kt-"
            f"{start_date:%Y%m%d}-seed{seed}-n{count}"
        ),
        "generated_at": generated_at,
        "imported_at": generated_at,
        "wind_metric": "max_wind_kt",
        "wind_metric_note": wind.load_catalog()["wind_metric_note"],
        "generator": {
            "package": "hurricane-simulator",
            "version": hurricane_simulator.__version__,
            "method": "StormGenerator.generate_ensemble",
            "historical_record": "NOAA HURDAT2 1851-2025, bundled with the package",
            "start": start,
            "seed": seed,
            "count": count,
            "member_seeds": ensemble.member_seeds,
        },
        "sampling_description": (
            f"{count} storms generated on request by hurricane_simulator "
            f"{hurricane_simulator.__version__}: an ensemble from one starting point "
            f"({latitude:.2f}, {longitude:.2f}), {max_wind_kt:g} kt on "
            f"{start_date.isoformat()}, base seed {seed}, using the analog model fit to "
            "NOAA HURDAT2 1851-2025. Tracks are complete, from the chosen start to "
            "dissipation."
        ),
        "completeness_warning": (
            "A what-if ensemble from one chosen starting point, not a probabilistic "
            "sample. Do not compute annual expected loss, average annual loss or "
            "exceedance probabilities from it."
        ),
        "storm_ids": [storm["storm_id"] for storm in storms],
        "storms": storms,
    }
