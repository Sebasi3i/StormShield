"""Storm generation on request: the hurricane simulator, loaded on first use.

Importing `hurricane_simulator` loads a global land/sea map of about 890 MB, and fitting
its analog model to the NOAA record takes a few seconds more. Neither happens when the
API starts: the simulator is imported and fitted on the first generate request, once,
even if several requests arrive together. Until then the API stays as light as it was;
after that, generating a storm takes milliseconds.

Two kinds of request are served:

  - `generate_catalog`: an ensemble from a starting point the caller chooses.
  - `florida_batch`: an ensemble from a starting point drawn at random from the
    historical record, kept only if enough of its members reach Florida as a major
    hurricane. The search over starting points is what makes the batch "hit Florida".

Generated storms come back in the stored catalog's own shape, so the dashboard draws
them and `POST /api/v1/storm-losses` prices them (via its `storms` field) exactly like
catalog storms. The API keeps no state: the client holds the storms it generated.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone

from . import florida, wind

# Upper bound on one request's ensemble. Each storm is also priced on demand, so this
# keeps a generate-then-price round trip to a couple of seconds.
MAX_STORMS_PER_REQUEST = 10

# Florida batch defaults. One-minute sustained wind of 96 kt is the Saffir-Simpson
# Category 3 threshold, the simulator's own bin edge (`schema.CATEGORY_BINS`).
MAJOR_HURRICANE_KT = 96.0
DEFAULT_BATCH_START_WIND_KT = 70.0
DEFAULT_MIN_FLORIDA_HITS = 2

# How many random starting points one batch request may try before giving up. At about
# 0.2 s per ten-member ensemble this bounds a request to roughly a minute; with the
# default 70 kt start wind the search typically ends within a few dozen starts.
MAX_STARTS_PER_BATCH = 400

_lock = threading.Lock()
_generator = None
_historical = None


def _load() -> None:
    global _generator, _historical
    if _generator is None:
        with _lock:
            if _generator is None:
                import hurricane_simulator

                historical = hurricane_simulator.prepare_historical_data(
                    hurricane_simulator.load_bundled_hurdat2()
                )
                _generator = hurricane_simulator.StormGenerator.fit(historical)
                _historical = historical


def storm_generator():
    """The fitted `hurricane_simulator.StormGenerator`, built on first use."""
    _load()
    return _generator


def historical_record():
    """The prepared NOAA HURDAT2 record the generator was fitted on."""
    _load()
    return _historical


def is_loaded() -> bool:
    return _generator is not None


def default_start_date() -> date:
    """10 September of the current year: close to the peak of the Atlantic season."""
    return date(datetime.now(timezone.utc).year, 9, 10)


def default_season_year() -> int:
    return datetime.now(timezone.utc).year


def _timestamp(value) -> str | None:
    if value is None or value != value:  # None or NaT
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _number(value, digits: int) -> float | None:
    if value is None or value != value:  # None or NaN
        return None
    return round(float(value), digits)


def _storms_from_ensemble(ensemble) -> list[dict]:
    """The members of a `GeneratedEnsemble` in the storm catalog's shape."""
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
    return storms


def _catalog(storms: list[dict], catalog_id: str, generator_block: dict, sampling: str, warning: str) -> dict:
    import hurricane_simulator

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "catalog_id": catalog_id,
        "generated_at": generated_at,
        "imported_at": generated_at,
        "wind_metric": "max_wind_kt",
        "wind_metric_note": wind.load_catalog()["wind_metric_note"],
        "generator": {
            "package": "hurricane-simulator",
            "version": hurricane_simulator.__version__,
            "historical_record": "NOAA HURDAT2 1851-2025, bundled with the package",
            **generator_block,
        },
        "sampling_description": sampling,
        "completeness_warning": warning,
        "storm_ids": [storm["storm_id"] for storm in storms],
        "storms": storms,
    }


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
    storms = _storms_from_ensemble(ensemble)
    start = {
        "latitude": latitude,
        "longitude": longitude,
        "max_wind_kt": max_wind_kt,
        "date": start_date.isoformat(),
    }
    return _catalog(
        storms,
        catalog_id=(
            f"generated-{latitude:.2f}_{longitude:.2f}-{max_wind_kt:g}kt-"
            f"{start_date:%Y%m%d}-seed{seed}-n{count}"
        ),
        generator_block={
            "method": "StormGenerator.generate_ensemble",
            "start": start,
            "seed": seed,
            "count": count,
            "member_seeds": ensemble.member_seeds,
        },
        sampling=(
            f"{count} storms generated on request by hurricane_simulator "
            f"{hurricane_simulator.__version__}: an ensemble from one starting point "
            f"({latitude:.2f}, {longitude:.2f}), {max_wind_kt:g} kt on "
            f"{start_date.isoformat()}, base seed {seed}, using the analog model fit to "
            "NOAA HURDAT2 1851-2025. Tracks are complete, from the chosen start to "
            "dissipation."
        ),
        warning=(
            "A what-if ensemble from one chosen starting point, not a probabilistic "
            "sample. Do not compute annual expected loss, average annual loss or "
            "exceedance probabilities from it."
        ),
    )


def florida_hit(storm: dict, min_wind_kt: float = MAJOR_HURRICANE_KT) -> dict:
    """Whether, where and how hard this storm's centre was over Florida at `min_wind_kt`
    or more.

    "Hits Florida" here means the storm's centre is over Florida land at major-hurricane
    strength at some track point: a storm that came ashore elsewhere first still counts,
    and one that weakened below the threshold before reaching Florida does not.
    """
    points = florida.florida_points(storm["track"], min_wind_kt)
    if not points:
        return {"florida_hit": False, "florida_peak_wind_kt": None, "florida_first_time": None}
    return {
        "florida_hit": True,
        "florida_peak_wind_kt": max(point["max_wind_kt"] for point in points),
        "florida_first_time": points[0]["timestamp"],
    }


def florida_batch(
    seed: int,
    count: int = MAX_STORMS_PER_REQUEST,
    max_wind_kt: float = DEFAULT_BATCH_START_WIND_KT,
    min_florida_hits: int = DEFAULT_MIN_FLORIDA_HITS,
    season_year: int | None = None,
) -> dict:
    """`count` storms from one starting point drawn at random from the historical
    record, of which at least `min_florida_hits` reach Florida as major hurricanes.

    The search: draw candidate starting points (a historical genesis position and date,
    jittered, as the simulator's own Monte Carlo run does), give each the requested
    start wind, run a `count`-member ensemble from it, and keep the first ensemble with
    enough Florida hits. Everything is derived from `seed`, so the same request in the
    same season year returns the same batch. The members that miss Florida are returned
    too: the batch shows the spread from one origin, with Florida hits flagged.

    Raises ValueError if no starting point within MAX_STARTS_PER_BATCH qualifies.
    """
    import hurricane_simulator
    import numpy as np
    from hurricane_simulator.genesis import sample_genesis

    if season_year is None:
        season_year = default_season_year()
    if not 1 <= min_florida_hits <= count:
        raise ValueError("min_florida_hits must be between 1 and count.")

    generator = storm_generator()
    candidates = sample_genesis(
        np.random.default_rng(seed), historical_record(), MAX_STARTS_PER_BATCH, season_year
    )
    ensemble_seeds = hurricane_simulator.spawn_member_seeds(seed, MAX_STARTS_PER_BATCH)

    starts_tried = 0
    for candidate, ensemble_seed in zip(candidates, ensemble_seeds):
        starts_tried += 1
        try:
            ensemble = generator.generate_ensemble(
                candidate.latitude,
                candidate.longitude,
                max_wind_kt,
                candidate.timestamp,
                ensemble_seed,
                count,
                storm_id_prefix=f"FL{seed}-",
            )
        except ValueError:
            # Jitter can put a historical genesis point on land or outside the
            # generator's region; the simulator says so, and the next candidate is tried.
            continue
        storms = _storms_from_ensemble(ensemble)
        for storm in storms:
            storm.update(florida_hit(storm))
        hits = [storm["storm_id"] for storm in storms if storm["florida_hit"]]
        if len(hits) >= min_florida_hits:
            break
    else:
        raise ValueError(
            f"No starting point produced {min_florida_hits} Florida major-hurricane "
            f"hits in {count} storms within {MAX_STARTS_PER_BATCH} tries for seed {seed}. "
            "Try another seed or a stronger start wind."
        )

    start = {
        "latitude": round(float(candidate.latitude), 4),
        "longitude": round(float(candidate.longitude), 4),
        "max_wind_kt": max_wind_kt,
        "date": candidate.timestamp.strftime("%Y-%m-%d"),
        "time": _timestamp(candidate.timestamp),
    }
    catalog = _catalog(
        storms,
        catalog_id=f"florida-batch-seed{seed}-n{count}-{max_wind_kt:g}kt-{season_year}",
        generator_block={
            "method": "StormGenerator.generate_ensemble from a random historical genesis point",
            "start": start,
            "seed": seed,
            "count": count,
            "member_seeds": ensemble.member_seeds,
            "season_year": season_year,
        },
        sampling=(
            f"{count} storms generated on request by hurricane_simulator "
            f"{hurricane_simulator.__version__}: an ensemble from one starting point "
            f"drawn at random from historical Atlantic genesis positions "
            f"({start['latitude']:.2f}, {start['longitude']:.2f}) on {start['date']}, "
            f"{max_wind_kt:g} kt, base seed {seed}. The starting point was the first of "
            f"{starts_tried} random candidates whose ensemble put at least "
            f"{min_florida_hits} storms over Florida at Category 3 or stronger "
            f"({MAJOR_HURRICANE_KT:g} kt one-minute sustained at the centre). Tracks "
            "are complete, from the start to dissipation."
        ),
        warning=(
            "A SELECTED batch: the starting point was chosen because its storms reach "
            "Florida as major hurricanes, and the members that miss are shown alongside. "
            "Not a probabilistic sample. Do not compute annual expected loss, average "
            "annual loss or exceedance probabilities from it."
        ),
    )
    catalog["florida"] = {
        "min_wind_kt": MAJOR_HURRICANE_KT,
        "criterion": (
            "Storm centre over Florida land with one-minute sustained wind of at least "
            f"{MAJOR_HURRICANE_KT:g} kt (Category 3) at some track point."
        ),
        "min_hits_requested": min_florida_hits,
        "hits": hits,
        "starts_tried": starts_tried,
        "max_starts": MAX_STARTS_PER_BATCH,
    }
    return catalog
