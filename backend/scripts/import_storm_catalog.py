"""Adapter: turn the hurricane simulator's CSV output into a storm catalog fixture.

The simulator is a separate package that we deliberately do not vendor. It emits two
tables (``synthetic_storm_tracks.csv``, ``synthetic_storm_summary.csv``) and, per its
own README, hands the next module nothing but ``storm_id, timestamp, latitude,
longitude, max_wind_kt``. This script is that boundary: it selects storms, copies only
those hazard columns, and records which run they came from.

Usage, from the backend directory:

    python scripts/import_storm_catalog.py <simulator_output_dir> --storms SYN0155,SYN0973

Re-run it when Adryel ships a new simulation. Nothing else in the backend changes.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

# Track points far from Florida cannot raise wind at a Florida property, and carrying
# the whole Atlantic basin would bloat the fixture. The window is generous so the
# frontend can still animate the approach, not just the landfall.
WINDOW = {"min_lat": 18.0, "max_lat": 35.0, "min_lon": -92.0, "max_lon": -72.0}

FLORIDA = {"min_lat": 24.3, "max_lat": 31.1, "min_lon": -87.7, "max_lon": -79.9}

FIXTURES = Path(__file__).resolve().parents[1] / "app" / "fixtures"


def _florida_landfalls(summary_path: Path, limit: int) -> list[dict]:
    """Strongest Florida landfalls in the run, which is the case worth demonstrating."""
    rows = []
    with summary_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["landfall"] != "True" or not row["landfall_lat"]:
                continue
            lat, lon = float(row["landfall_lat"]), float(row["landfall_lon"])
            if not FLORIDA["min_lat"] <= lat <= FLORIDA["max_lat"]:
                continue
            if not FLORIDA["min_lon"] <= lon <= FLORIDA["max_lon"]:
                continue
            rows.append(row)
    rows.sort(key=lambda r: float(r["landfall_wind_kt"]), reverse=True)
    return rows[:limit]


def _optional_float(value: str, digits: int = 4) -> float | None:
    return round(float(value), digits) if value else None


def build(output_dir: Path, storm_ids: list[str] | None, limit: int) -> dict:
    summary_path = output_dir / "synthetic_storm_summary.csv"
    tracks_path = output_dir / "synthetic_storm_tracks.csv"
    for path in (summary_path, tracks_path):
        if not path.exists():
            raise SystemExit(f"missing {path}")

    with summary_path.open(newline="", encoding="utf-8") as handle:
        run_storm_count = sum(1 for _ in csv.DictReader(handle))

    if storm_ids:
        with summary_path.open(newline="", encoding="utf-8") as handle:
            found = {
                r["storm_id"]: r
                for r in csv.DictReader(handle)
                if r["storm_id"] in storm_ids
            }
        missing = [sid for sid in storm_ids if sid not in found]
        if missing:
            raise SystemExit(f"not in {summary_path.name}: " + ", ".join(missing))
        selected = [found[sid] for sid in storm_ids]
        selection_note = "hand-picked storm ids " + ", ".join(storm_ids)
    else:
        selected = _florida_landfalls(summary_path, limit)
        selection_note = (
            f"the {len(selected)} strongest Florida landfalls in the run, "
            "ranked by landfall_wind_kt"
        )

    tracks: dict[str, list[dict]] = {row["storm_id"]: [] for row in selected}
    with tracks_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["storm_id"] not in tracks:
                continue
            lat, lon = float(row["latitude"]), float(row["longitude"])
            if not WINDOW["min_lat"] <= lat <= WINDOW["max_lat"]:
                continue
            if not WINDOW["min_lon"] <= lon <= WINDOW["max_lon"]:
                continue
            tracks[row["storm_id"]].append(
                {
                    "step": int(row["step"]),
                    "timestamp": row["timestamp"],
                    "latitude": round(lat, 4),
                    "longitude": round(lon, 4),
                    "max_wind_kt": round(float(row["max_wind_kt"]), 1),
                    "category": row["category"],
                    "is_over_land": row["is_over_land"] == "True",
                }
            )

    storms = [
        {
            "storm_id": row["storm_id"],
            "genesis_time": row["genesis_time"],
            "peak_wind_kt": round(float(row["max_wind_kt"]), 1),
            "duration_hours": float(row["duration_hours"]),
            "landfall": row["landfall"] == "True",
            "landfall_time": row["landfall_time"] or None,
            "landfall_lat": _optional_float(row["landfall_lat"]),
            "landfall_lon": _optional_float(row["landfall_lon"]),
            "landfall_wind_kt": _optional_float(row["landfall_wind_kt"], 1),
            "track": tracks[row["storm_id"]],
        }
        for row in selected
    ]

    return {
        "catalog_id": f"syn-fl-{len(storms)}-v1",
        "imported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "wind_metric": "max_wind_kt",
        "wind_metric_note": (
            "One-minute sustained wind at the storm CENTRE in knots, HURDAT2 "
            "convention. This is not wind at a property and it is not a gust. "
            "Converting it to a property-level peak gust is app/wind.py's job, and "
            "that conversion is an assumption."
        ),
        "sampling_description": (
            f"hurricane_simulator V1 Monte Carlo run of {run_storm_count} synthetic "
            "Atlantic storms bootstrapped from HURDAT2 1851-2025 at a 6-hour time "
            f"step. This catalog holds {selection_note}. Track points are clipped to "
            f"lat {WINDOW['min_lat']} to {WINDOW['max_lat']}, lon "
            f"{WINDOW['min_lon']} to {WINDOW['max_lon']}."
        ),
        "completeness_warning": (
            "A SELECTED subset, not a probabilistic sample: these storms were chosen "
            "because they hit Florida hard. Do not compute annual expected loss, "
            "average annual loss or exceedance probabilities from it. That needs the "
            "full catalog and the annual event rates Adryel owns."
        ),
        "source_run": str(output_dir),
        "storm_ids": [s["storm_id"] for s in storms],
        "storms": storms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import simulator output as a catalog.")
    parser.add_argument("output_dir", type=Path, help="the simulator's output/ directory")
    parser.add_argument("--storms", help="comma-separated ids; default picks FL landfalls")
    parser.add_argument("--limit", type=int, default=3, help="how many to auto-select")
    parser.add_argument("--out", type=Path, default=FIXTURES / "storm_catalog.json")
    args = parser.parse_args()

    storm_ids = [s.strip() for s in args.storms.split(",")] if args.storms else None
    catalog = build(args.output_dir, storm_ids, args.limit)
    args.out.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {args.out}")
    for storm in catalog["storms"]:
        peak = storm["peak_wind_kt"]
        landfall = storm["landfall_wind_kt"]
        print(
            f"  {storm['storm_id']}  peak {peak} kt  landfall {landfall} kt at "
            f"{storm['landfall_lat']}, {storm['landfall_lon']}  "
            f"{len(storm['track'])} points in window"
        )


if __name__ == "__main__":
    main()
