"""The storm-size model: its fit is reproducible from the bundled record, and the wind
adapter applies it per storm.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from app import wind

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _fit_script():
    spec = importlib.util.spec_from_file_location(
        "fit_storm_size", BACKEND / "scripts" / "fit_storm_size.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def model() -> dict:
    return wind.load_storm_size_model()


def test_the_fixture_is_reproducible_from_the_bundled_record(model):
    """Re-running the fit on the HURDAT2 file shipped with the simulator gives the
    committed coefficients, so the fixture is derived, not typed in."""
    script = _fit_script()
    refit, table = script.build_model(script.bundled_hurdat2())

    assert refit["source"]["file"] == model["source"]["file"]
    for key in ("intercept", "per_kt", "per_degree_latitude"):
        assert refit["rmw"][key] == pytest.approx(model["rmw"][key], abs=1e-9)
    assert refit["rmw"]["fixes"] == model["rmw"]["fixes"]
    assert refit["outer_decay_exponent"]["value"] == model["outer_decay_exponent"]["value"]
    assert refit["taper"] == model["taper"]
    assert len(table) > 50000


def test_the_fit_rests_on_a_real_sample(model):
    assert model["rmw"]["fixes"] >= 500
    assert model["rmw"]["storms"] >= 30
    assert model["rmw"]["seasons"][0] == 2021
    assert model["outer_decay_exponent"]["fixes"] >= 400
    assert model["taper"]["fixes"] >= 2000


def test_a_stronger_storm_has_a_smaller_eye(model):
    assert model["rmw"]["per_kt"] < 0
    assert wind.rmw_km(140.0, 26.0) < wind.rmw_km(100.0, 26.0) < wind.rmw_km(65.0, 26.0)


def test_a_higher_latitude_storm_has_a_larger_eye(model):
    assert model["rmw"]["per_degree_latitude"] > 0
    assert wind.rmw_km(100.0, 35.0) > wind.rmw_km(100.0, 20.0)


def test_rmw_matches_the_record_for_a_major_hurricane():
    # Hurricane Ian at its Florida landfall: 130-140 kt at 26-27 N, best-track RMW
    # 20 nautical miles (37 km). The fit places a storm like it in the 20-30 km band,
    # inside the residual spread of the fit; a 30 km constant is no longer applied.
    assert 18.0 <= wind.rmw_km(135.0, 26.7) <= 32.0


def test_rmw_is_clamped_to_the_record_range(model):
    low, high = model["rmw"]["bounds_km"]
    assert wind.rmw_km(185.0, 5.0) == low
    assert wind.rmw_km(64.0, 60.0) == high


def test_decay_exponent_and_taper_come_from_the_record(model):
    assert 0.3 <= model["outer_decay_exponent"]["value"] <= 0.7
    assert 200.0 <= model["taper"]["taper_start_km"] < model["taper"]["cutoff_km"] <= 600.0


def _storm(storm_id, *points):
    return {
        "storm_id": storm_id,
        "track": [
            {"step": step, "timestamp": f"2026-09-01 {6 * step:02d}:00:00", "latitude": latitude,
             "longitude": longitude, "max_wind_kt": knots, "category": "4", "is_over_land": False}
            for step, (latitude, longitude, knots) in enumerate(points)
        ],
    }


def test_storm_parameters_use_the_peak_intensity_point_near_florida(model):
    # 130 kt out at 75 W is outside the Florida window; the 110 kt fix off Florida is
    # the one that sizes the storm.
    storm = _storm("T", (22.0, -70.0, 90.0), (24.0, -75.0, 130.0), (26.0, -80.0, 110.0))
    parameters = wind.storm_parameters(storm)

    assert parameters["storm_id"] == "T"
    assert parameters["size_basis"] == {"peak_wind_kt": 110.0, "latitude": 26.0, "timestamp": "2026-09-01 12:00:00"}
    assert parameters["rmw_km"] == pytest.approx(wind.rmw_km(110.0, 26.0), abs=0.05)
    # The decay exponent is the station-calibrated one, not the radii median.
    assert parameters["outer_decay_exponent"] == wind.load_wind_calibration()["outer_decay_exponent"]
    assert parameters["taper_start_km"] == model["taper"]["taper_start_km"]
    assert parameters["cutoff_km"] == model["taper"]["cutoff_km"]
    assert parameters["parameter_status"] == "sourced"
    assert model["storm_size_model_id"] in parameters["source_note"]


def test_two_storms_of_different_strength_get_different_sizes():
    weak = wind.storm_parameters(_storm("W", (25.0, -80.0, 70.0)))
    strong = wind.storm_parameters(_storm("S", (25.0, -80.0, 145.0)))

    assert strong["rmw_km"] < weak["rmw_km"]


def test_the_size_reaches_the_exposure_detail():
    storm = _storm("D", (25.0, -80.0, 120.0))
    _, detail = wind.exposures_for_storm(storm, [("HOME", 25.3, -80.0)])

    assert detail[0]["rmw_km"] == wind.storm_parameters(storm)["rmw_km"]


def test_metadata_publishes_the_model_not_a_constant():
    parameters = wind.metadata()["storm_parameters"]

    assert parameters["parameter_status"] == "sourced"
    assert parameters["storm_size_model_id"] == "hurdat2-radii-fit-v1"
    assert "rmw_km" not in parameters, "size is per storm now, not one number"
    assert parameters["fit"]["rmw_fixes"] >= 500
    assert "validation" in parameters["validation_status"]
