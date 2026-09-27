"""Simulate a large, unselected sample of Atlantic storms and record the peak gust each
one brings to every demo property.

The stored catalog and the Florida batch are SELECTED sets: storms kept because they
hit Florida hard. Averages over them say nothing about how often such storms come. This
script asks the simulator for the other thing it can do: `simulate_hurricanes` draws
every storm's starting point at random from the whole historical record (1,988 storms,
1851-2025), so each synthetic storm is "a random Atlantic storm" and a large sample is
a fair one. Most of them never come near Florida, which is exactly the information a
frequency assumption needs and an invented probability lacks.

For each storm the wind step (app/wind.py, the calibrated wind field) gives the peak
gust at each of the ten demo properties. Only those gusts are stored, not the tracks:
pricing from a gust is instant, so the insurer service can re-price any project
selection, deductible or premium plan against the whole sample on request without
re-running wind. A storm whose six-hourly track never comes within the wind model's
cutoff plus a generous margin of any property is recorded as zero at every property
without running the wind field, which is what the field would return anyway.

Expected yearly figures need a storms-per-year rate for the same population the
simulator samples from. Two are recorded: the whole record's, which under-counts the
pre-satellite era, and the last thirty complete years', the defensible default.

    python scripts/build_storm_climatology.py                 # 5,000 storms, seed 2026
    python scripts/build_storm_climatology.py --storms 500    # a quick, noisy sample

Writes app/fixtures/storm_climatology.json. Takes minutes for thousands of storms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import wind  # noqa: E402

FIXTURES = BACKEND / "app" / "fixtures"
PORTFOLIO = FIXTURES / "example_portfolio.json"
OUTPUT = FIXTURES / "storm_climatology.json"

DEFAULT_STORMS = 5000
DEFAULT_SEED = 2026
SEASON_YEAR = 2026
RECENT_YEARS = 30

# A track point can be this much closer to a property than the six-hourly fixes
# around it; a storm moving at 20 kt covers about 220 km between fixes. Storms whose
# every fix is farther than the cutoff plus this margin from every property are skipped.
PRUNE_MARGIN_KM = 300.0
EARTH_RADIUS_KM = 6371.0088


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(min(1.0, a)))


def storms_per_year(prepared, years: int | None) -> dict:
    """Storms in the simulator's source record per year, over the whole record or the
    last `years` complete years."""
    first_by_storm = prepared.groupby("storm_id")["timestamp"].min()
    storm_years = first_by_storm.dt.year
    last_year = int(storm_years.max())
    if years is None:
        start = int(storm_years.min())
    else:
        start = last_year - years + 1
    count = int((storm_years >= start).sum())
    span = last_year - start + 1
    return {"from_year": start, "to_year": last_year, "storms": count, "years": span, "storms_per_year": round(count / span, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--storms", type=int, default=DEFAULT_STORMS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    import hurricane_simulator

    portfolio = json.loads(PORTFOLIO.read_text(encoding="utf-8"))
    properties = [(p["property_id"], p["latitude"], p["longitude"]) for p in portfolio["properties"]]

    started = time.time()
    historical = hurricane_simulator.prepare_historical_data(hurricane_simulator.load_bundled_hurdat2())
    result = hurricane_simulator.simulate_hurricanes(historical, num_storms=args.storms, seed=args.seed, season_year=SEASON_YEAR)
    print(f"simulated {args.storms} storms in {time.time() - started:.0f}s", flush=True)

    size_model = wind.load_storm_size_model()
    cutoff_km = float(size_model["taper"]["cutoff_km"])
    prune_km = cutoff_km + PRUNE_MARGIN_KM

    storms = []
    computed = 0
    t0 = time.time()
    for index, (storm_id, track) in enumerate(result.tracks.groupby("storm_id", sort=False), start=1):
        points = [
            {
                "step": int(i),
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "latitude": round(float(row.latitude), 4),
                "longitude": round(float(row.longitude), 4),
                "max_wind_kt": round(float(row.max_wind_kt), 1),
            }
            for i, (ts, row) in enumerate(zip(track["timestamp"], track.itertuples()))
        ]
        closest = min(
            haversine_km(pt["latitude"], pt["longitude"], lat, lon) for pt in points for _, lat, lon in properties
        )
        peak_kt = max(pt["max_wind_kt"] for pt in points)
        landfall = bool(track["is_over_land"].any()) if "is_over_land" in track else None
        if closest > prune_km:
            gusts = [0.0] * len(properties)
        else:
            exposures, _ = wind.exposures_for_storm({"storm_id": storm_id, "track": points}, properties)
            gusts = [round(float(e.peak_gust_mph), 2) for e in exposures]
            computed += 1
        storms.append(
            {
                "storm_id": storm_id,
                "peak_wind_kt": peak_kt,
                "closest_fix_km": round(closest, 1),
                "landfall": landfall,
                "gusts_mph": gusts,
            }
        )
        if index % 500 == 0:
            print(f"  {index}/{args.storms} storms, {computed} through the wind field, {time.time() - t0:.0f}s", flush=True)

    max_gust = max(g for s in storms for g in s["gusts_mph"])
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metadata = wind.metadata()
    payload = {
        "climatology_id": f"mc-atlantic-{args.storms}-seed{args.seed}-v1",
        "generated_at": generated_at,
        "script": "backend/scripts/build_storm_climatology.py",
        "evidence_status": "simulated",
        "summary": (
            f"{args.storms} synthetic Atlantic storms from hurricane_simulator's Monte Carlo (each start "
            "drawn at random from the whole historical record, so the sample is unselected), each run "
            "through the calibrated wind field to get the peak gust at every demo property. Not a "
            "selected set: most storms never approach Florida, and that is the point."
        ),
        "simulator": {
            "package": "hurricane-simulator",
            "version": hurricane_simulator.__version__,
            "method": "simulate_hurricanes",
            "seed": args.seed,
            "num_storms": args.storms,
            "season_year": SEASON_YEAR,
            "historical_record": "NOAA HURDAT2 1851-2025, bundled with the package",
        },
        "storms_per_year": {
            "note": (
                "Storms in the simulator's source record per year, for the same population it samples "
                "genesis from. The whole record under-counts storms before aircraft and satellites; "
                "the last thirty complete years are the defensible default."
            ),
            "default": "recent",
            "whole_record": storms_per_year(historical, None),
            "recent": storms_per_year(historical, RECENT_YEARS),
        },
        "wind_model": {
            "wind_metric": metadata["wind_metric"],
            "wind_calibration_id": metadata["calibration"]["wind_calibration_id"],
            "storm_size_model_id": metadata["storm_parameters"]["storm_size_model_id"],
            "gust_factor": metadata["gust_factor"],
            "land_exposure_factor": metadata["land_exposure_factor"],
            "negligible_gust_floor_mph": metadata["negligible_gust_floor_mph"],
        },
        "pruning": {
            "rule": f"a storm whose every six-hourly fix is more than {prune_km:g} km (cutoff {cutoff_km:g} km + {PRUNE_MARGIN_KM:g} km margin) from every property is recorded as 0 mph everywhere without running the wind field",
            "storms_through_wind_field": computed,
            "storms_pruned": args.storms - computed,
        },
        "portfolio": {
            "portfolio_id": portfolio["portfolio_id"],
            "example_portfolio_sha256": sha256(PORTFOLIO),
            "property_ids": [pid for pid, _, _ in properties],
            "coordinates": {pid: [lat, lon] for pid, lat, lon in properties},
            "scope": "Gusts are stored for these properties only; a property added to the portfolio needs this fixture rebuilt.",
        },
        "input_hashes_sha256": {
            name: sha256(FIXTURES / name)
            for name in ("storm_size_model.json", "gust_factor_model.json", "wind_calibration.json")
        },
        "max_gust_mph": max_gust,
        "gust_columns": [pid for pid, _, _ in properties],
        "storms": storms,
    }
    args.output.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    any_gust = sum(1 for s in storms if any(g > 0 for g in s["gusts_mph"]))
    print(
        f"wrote {args.output} ({args.output.stat().st_size / 1e6:.1f} MB): {args.storms} storms, "
        f"{computed} through the wind field, {any_gust} with any gust, max gust {max_gust} mph, "
        f"{payload['storms_per_year']['recent']['storms_per_year']} storms/yr (recent) in {time.time() - started:.0f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
