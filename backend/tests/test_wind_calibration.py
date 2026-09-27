"""The gust factor, the profile calibration and the validation record: each fixture is
reproducible from the committed station data, and the adapter applies them.

The refits take about a minute together, so they run once per module.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

from app import wind

BACKEND = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = BACKEND / "app" / "fixtures"
SCRIPTS = BACKEND / "scripts"


def _script(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gust_model() -> dict:
    return wind.load_gust_factor_model()


@pytest.fixture(scope="module")
def calibration() -> dict:
    return wind.load_wind_calibration()


@pytest.fixture(scope="module")
def validation() -> dict:
    return json.loads((FIXTURES / "wind_validation.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Gust factor
# --------------------------------------------------------------------------- #


def test_the_gust_factor_is_reproducible_from_the_station_pairs(gust_model):
    script = _script("fit_gust_factor")
    refit = script.build_model(script.PAIRS)

    assert refit["gust_factor"] == gust_model["gust_factor"]
    assert refit["land_fetch"] == gust_model["land_fetch"]
    assert refit["ocean_fetch"] == gust_model["ocean_fetch"]
    assert refit["selection"] == gust_model["selection"]


def test_the_gust_factor_rests_on_hurricane_observations(gust_model):
    assert gust_model["selection"]["storms"] == 11
    assert gust_model["selection"]["stations"] >= 40
    assert gust_model["land_fetch"]["n"] >= 5000
    assert gust_model["selection"]["min_mean2min_kt"] == 34.0
    assert gust_model["selection"]["parser_repaired_rows_excluded"] is True


def test_land_fetch_gusts_more_than_ocean_fetch(gust_model):
    assert gust_model["land_fetch"]["median"] > gust_model["ocean_fetch"]["median"]
    assert gust_model["gust_factor"] == gust_model["land_fetch"]["median"]


def test_the_adapter_uses_the_measured_gust_factor(gust_model):
    assert wind.GUST_FACTOR == gust_model["gust_factor"]
    assert 1.25 < wind.GUST_FACTOR < 1.45
    assert wind.metadata()["gust_factor"] == wind.GUST_FACTOR
    assert gust_model["gust_factor_model_id"] in wind.metadata()["gust_factor_note"]


# --------------------------------------------------------------------------- #
# Profile calibration
# --------------------------------------------------------------------------- #


def test_the_calibration_is_reproducible_from_the_station_peaks(calibration):
    script = _script("calibrate_wind_field")
    refit = script.calibrate()

    assert refit["outer_decay_exponent"] == calibration["outer_decay_exponent"]
    assert refit["land_exposure_factor"] == calibration["land_exposure_factor"]
    assert refit["chosen"] == calibration["chosen"]
    assert refit["data"]["station_storm_pairs"] == calibration["data"]["station_storm_pairs"]


def test_the_chosen_point_meets_the_objective(calibration):
    chosen = calibration["chosen"]
    assert abs(chosen["median_ratio"] - 1.0) <= 0.05
    assert abs(chosen["median_ratio_observed_64kt_or_more"] - 1.0) <= 0.10
    assert chosen["mean_absolute_error_kt"] < calibration["uncalibrated_reference"]["mean_absolute_error_kt"]
    # Not at the edge of the search grid, so the optimum is inside it.
    grid = calibration["grid"]
    assert calibration["outer_decay_exponent"] not in (grid["outer_decay_exponent"][0], grid["outer_decay_exponent"][-1])
    assert calibration["land_exposure_factor"] not in (grid["land_exposure_factor"][0], grid["land_exposure_factor"][-1])


def test_the_calibration_is_the_same_data_as_the_gust_factor(calibration, gust_model):
    assert calibration["gust_factor"] == gust_model["gust_factor"]
    assert calibration["gust_factor_model_id"] == gust_model["gust_factor_model_id"]
    assert calibration["data"]["storms"] == 11


def _storm(storm_id, *points):
    return {
        "storm_id": storm_id,
        "track": [
            {"step": step, "timestamp": f"2026-09-01 {6 * step:02d}:00:00", "latitude": latitude,
             "longitude": longitude, "max_wind_kt": knots, "category": "4", "is_over_land": False}
            for step, (latitude, longitude, knots) in enumerate(points)
        ],
    }


def test_the_adapter_applies_the_calibrated_decay_and_land_factor(calibration):
    storm = _storm("C", (25.0, -80.0, 120.0))
    parameters = wind.storm_parameters(storm)
    assert parameters["outer_decay_exponent"] == calibration["outer_decay_exponent"]
    assert calibration["wind_calibration_id"] in parameters["source_note"]

    _, detail = wind.exposures_for_storm(storm, [("HOME", 25.3, -80.0)])
    assert detail[0]["land_exposure_factor"] == calibration["land_exposure_factor"]
    assert 0.5 < calibration["land_exposure_factor"] < 1.0


def test_size_is_taken_near_florida_not_at_a_distant_peak():
    # Peaks at 150 kt far out in the Atlantic, then 110 kt off Florida: the 110 kt
    # point sizes the storm.
    storm = _storm("F", (18.0, -55.0, 150.0), (24.0, -66.0, 130.0), (26.0, -80.5, 110.0))
    basis = wind.size_basis_point(storm)
    assert (basis["max_wind_kt"], basis["latitude"]) == (110.0, 26.0)
    assert wind.storm_parameters(storm)["rmw_km"] == pytest.approx(wind.rmw_km(110.0, 26.0), abs=0.05)

    # A storm that never comes near Florida is sized at its overall peak.
    far = _storm("A", (18.0, -55.0, 150.0), (30.0, -60.0, 90.0))
    assert wind.size_basis_point(far)["max_wind_kt"] == 150.0


# --------------------------------------------------------------------------- #
# Validation record
# --------------------------------------------------------------------------- #


def test_the_validation_record_matches_the_current_adapter(validation):
    script = _script("validate_wind_field")
    rerun, _ = script.validate()

    assert rerun["gust_factor"] == validation["gust_factor"]
    assert rerun["all"] == validation["all"]
    assert rerun["within_75_km"] == validation["within_75_km"]
    assert rerun["observed_64kt_or_more"] == validation["observed_64kt_or_more"]
    assert rerun["selection"] == validation["selection"]


def test_validation_covers_the_storms_and_publishes_its_errors(validation):
    assert validation["selection"]["storms"] == 11
    assert validation["selection"]["station_storm_pairs"] >= 100
    assert validation["all"]["mean_absolute_error_kt"] > 0
    assert 0.9 <= validation["all"]["median_ratio_modeled_over_observed"] <= 1.1
    assert len(validation["truncated_records"]) >= 1


def test_metadata_carries_the_validation_headline(validation):
    published = wind.metadata()["validation"]
    assert published["wind_validation_id"] == validation["wind_validation_id"]
    assert published["mean_absolute_error_kt"] == validation["all"]["mean_absolute_error_kt"]
    assert wind.metadata()["evidence_status"] == "sourced"
    assert wind.metadata()["land_exposure_factor"] == wind.load_wind_calibration()["land_exposure_factor"]
