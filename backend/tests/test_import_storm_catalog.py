"""The catalog import: tracks are trimmed at their ends, never cut in the middle.

Clipping point by point once removed SYN0155's single step north of 35N from the middle
of its track, leaving a 12-hour hole that the wind field refuses to bridge. These pin the
fix, including an end-to-end import that is fed straight to the wind field.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import csv
import importlib.util
import pathlib
from datetime import datetime, timedelta

from app import wind

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "import_storm_catalog.py"
_spec = importlib.util.spec_from_file_location("import_storm_catalog", SCRIPT)
importer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(importer)

# (latitude, longitude): outside, inside, outside (north of 35N), inside, outside.
PATH = [(12.0, -50.0), (25.0, -80.0), (36.0, -76.0), (34.0, -75.0), (45.0, -60.0)]


def _points(path):
    start = datetime(2026, 9, 1)
    return [
        {
            "step": step,
            "timestamp": (start + timedelta(hours=6 * step)).strftime("%Y-%m-%d %H:%M:%S"),
            "latitude": latitude,
            "longitude": longitude,
        }
        for step, (latitude, longitude) in enumerate(path)
    ]


def test_an_excursion_outside_the_window_is_kept_not_cut_out():
    kept = importer._trim_to_window(_points(PATH))
    assert [point["step"] for point in kept] == [1, 2, 3]


def test_a_storm_that_never_enters_the_window_has_an_empty_track():
    assert importer._trim_to_window(_points([(12.0, -50.0), (45.0, -60.0)])) == []


def test_an_imported_track_has_no_gaps_and_prices_without_warnings(tmp_path):
    summary_fields = [
        "storm_id", "genesis_time", "max_wind_kt", "duration_hours", "landfall",
        "landfall_time", "landfall_lat", "landfall_lon", "landfall_wind_kt",
    ]
    with (tmp_path / "synthetic_storm_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerow({
            "storm_id": "SYN0001", "genesis_time": "2026-09-01 00:00:00", "max_wind_kt": 120,
            "duration_hours": 24, "landfall": "True", "landfall_time": "2026-09-01 06:00:00",
            "landfall_lat": 25.0, "landfall_lon": -80.0, "landfall_wind_kt": 110,
        })

    track_fields = [
        "storm_id", "step", "timestamp", "latitude", "longitude", "max_wind_kt",
        "category", "is_over_land",
    ]
    with (tmp_path / "synthetic_storm_tracks.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=track_fields)
        writer.writeheader()
        for point in _points(PATH):
            writer.writerow({**point, "storm_id": "SYN0001", "max_wind_kt": 110,
                             "category": "3", "is_over_land": "False"})

    catalog = importer.build(tmp_path, ["SYN0001"], limit=1)
    storm = catalog["storms"][0]

    steps = [point["step"] for point in storm["track"]]
    assert steps == [1, 2, 3]
    assert wind.track_gaps(storm) == []
    exposures, _ = wind.exposures_for_storm(storm, [("P001", 25.7617, -80.1918)])
    assert exposures[0].peak_gust_mph > 0
