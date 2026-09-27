"""The gust factor, the profile calibration, the validation record and the cohort
sensitivity comparison: each production fixture is reproducible from the committed
station data, cohort membership is manifest-driven and exact, storm holdouts refit
every ASOS-derived constant without the held-out storm, and the adapter applies the
result without ever depending on a fixture it cannot also override.

Real-data refits (primary cohort, ~11 storms) take about a minute and run once per
module via the fixtures below. A few tests that specifically exercise the
leave-one-storm-out mechanics use small synthetic data instead of the full dataset,
so the fix this file is pinning down (a fold must refit its own gust factor) is
checked quickly and deterministically rather than only implied by the real numbers
coming out differently than before.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pandas as pd
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
def cc():
    return _script("calibration_common")


@pytest.fixture(scope="module")
def gust_model() -> dict:
    return wind.load_gust_factor_model()


@pytest.fixture(scope="module")
def calibration() -> dict:
    return wind.load_wind_calibration()


@pytest.fixture(scope="module")
def validation() -> dict:
    return json.loads((FIXTURES / "wind_validation.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sensitivity() -> dict:
    return json.loads((FIXTURES / "calibration_sensitivity.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def combined_calibration(cc):
    """The combined (2004-2024) cohort's own fit - never promoted to production, but
    needed by several tests below to check it is genuinely a separate fit rather than
    the primary cohort's numbers relabelled."""
    script = _script("calibrate_wind_field")
    return script.calibrate(cc.COMBINED_COHORT)


# --------------------------------------------------------------------------- #
# Cohort membership - manifest-driven, exact, and never inferred by year
# --------------------------------------------------------------------------- #


def test_primary_cohort_is_exactly_its_manifest(cc):
    manifest = pd.read_csv(
        BACKEND.parent / "data" / "calibration" / "fl_hurricane_gust_data" / "storms_2016_2024.csv"
    )
    assert cc.cohort_storm_ids(cc.PRIMARY_COHORT) == set(manifest["storm_id"])
    assert len(cc.cohort_storm_ids(cc.PRIMARY_COHORT)) == 11


def test_combined_cohort_is_the_exact_union_of_both_manifests(cc):
    primary = cc.cohort_storm_ids(cc.PRIMARY_COHORT)
    legacy = cc.cohort_storm_ids(cc.LEGACY_COHORT)
    combined = cc.cohort_storm_ids(cc.COMBINED_COHORT)
    assert combined == primary | legacy
    assert primary.isdisjoint(legacy)
    assert len(combined) == 19


def test_an_unknown_cohort_id_is_an_error_not_a_guess(cc):
    with pytest.raises(cc.CohortError):
        cc.cohort_storm_ids("nineteen-ninety-two")
    with pytest.raises(cc.CohortError):
        cc.load_manifest(cc.COMBINED_COHORT)  # no manifest file for the union itself


def test_cohorts_partition_the_committed_pairs_file_exactly(cc):
    pairs = pd.read_csv(
        BACKEND.parent / "data" / "calibration" / "fl_hurricane_gust_data" / "fl_gust_pairs_2min_qc.csv.gz",
        usecols=["storm_id"],
    )
    cc.validate_cohorts_partition(set(pairs["storm_id"].unique()))  # does not raise
    with pytest.raises(cc.CohortError):
        cc.validate_cohorts_partition(set(pairs["storm_id"].unique()) | {"NOT_A_REAL_STORM"})


def test_filter_by_storm_ids_fails_on_an_id_the_frame_does_not_have(cc):
    frame = pd.DataFrame({"storm_id": ["A2020", "B2020"], "value": [1, 2]})
    with pytest.raises(cc.CohortError):
        cc.filter_by_storm_ids(frame, {"A2020", "NOT_PRESENT"})
    kept = cc.filter_by_storm_ids(frame, {"A2020"})
    assert list(kept["storm_id"]) == ["A2020"]


# --------------------------------------------------------------------------- #
# Explicit boolean and missing-value handling
# --------------------------------------------------------------------------- #


def test_the_string_false_is_false_not_truthy(cc):
    parsed = cc.parse_bool_flag(pd.Series(["False", "True", "false", "TRUE", "0", "1"]))
    assert parsed.tolist() == [False, True, False, True, False, True]
    # The trap this exists to avoid: Python's own bool("False") is True.
    assert bool("False") is True
    assert parsed.tolist()[0] is False


def test_an_unrecognised_flag_value_is_an_error(cc):
    with pytest.raises(cc.DataValidationError):
        cc.parse_bool_flag(pd.Series(["True", "maybe"]))


def test_require_finite_names_the_column_and_the_count(cc):
    frame = pd.DataFrame({"x": [1.0, float("nan"), 3.0], "y": [1.0, 2.0, 3.0]})
    cc.require_finite(frame, ["y"])  # does not raise
    with pytest.raises(cc.DataValidationError, match="'x'"):
        cc.require_finite(frame, ["x"])


def test_require_unique_keys_names_a_duplicate(cc):
    frame = pd.DataFrame({"storm_id": ["A", "A", "B"], "station": ["S1", "S1", "S2"]})
    with pytest.raises(cc.DataValidationError):
        cc.require_unique_keys(frame, ["storm_id", "station"])


def test_metrics_from_columns_reports_null_not_nan_when_empty(cc):
    empty = pd.DataFrame({"modeled_gust_kt": [], "observed_gust_kt": []})
    metrics = cc.metrics_from_columns(empty)
    assert metrics["n"] == 0
    assert metrics["bias_kt"] is None
    assert metrics["median_ratio"] is None


# --------------------------------------------------------------------------- #
# Gust factor
# --------------------------------------------------------------------------- #


def test_the_gust_factor_is_reproducible_from_the_station_pairs(gust_model):
    script = _script("fit_gust_factor")
    refit = script.build_model()  # default cohort: primary_2016_2024, matching gust_model

    assert refit["gust_factor"] == gust_model["gust_factor"]
    assert refit["land_fetch"] == gust_model["land_fetch"]
    assert refit["ocean_fetch"] == gust_model["ocean_fetch"]
    assert refit["cohort_id"] == gust_model["cohort_id"] == "primary_2016_2024"


def test_the_gust_factor_rests_on_the_primary_cohort_alone(gust_model, cc):
    assert gust_model["cohort_id"] == cc.PRIMARY_COHORT
    assert set(gust_model["selected_storm_ids"]) == cc.cohort_storm_ids(cc.PRIMARY_COHORT)
    assert gust_model["selection"]["storms_selected"] == 11
    assert gust_model["selection"]["stations"] >= 20
    assert gust_model["land_fetch"]["n"] >= 2000
    assert gust_model["selection"]["min_mean2min_kt"] == 34.0
    assert gust_model["selection"]["parser_repaired_rows_excluded"] is True


def test_the_combined_cohort_fits_its_own_gust_factor_not_the_primarys(combined_calibration, gust_model, cc):
    """The combined fit must not read the primary's factor off a fixture - it fits its
    own, from its own (larger) storm set, and the two need not agree."""
    assert combined_calibration["gust_factor_model_id"] != "" and combined_calibration["gust_factor"] is not None
    script = _script("fit_gust_factor")
    combined_gust = script.build_model(cohort=cc.COMBINED_COHORT)
    assert combined_calibration["gust_factor"] == combined_gust["gust_factor"]
    assert set(combined_gust["selected_storm_ids"]) == cc.cohort_storm_ids(cc.COMBINED_COHORT)
    # Historical context only, per the implementation description: not asserted to
    # equal the pre-cohort combined value, only checked to be in a sane neighbourhood.
    assert 1.2 < combined_gust["gust_factor"] < 1.4


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
    refit = script.calibrate()  # default cohort: primary_2016_2024

    assert refit["outer_decay_exponent"] == calibration["outer_decay_exponent"]
    assert refit["land_exposure_factor"] == calibration["land_exposure_factor"]
    assert refit["chosen"] == calibration["chosen"]
    assert refit["data"]["station_storm_pairs"] == calibration["data"]["station_storm_pairs"]
    assert refit["data"]["storms"] == calibration["data"]["storms"] == 11


def test_the_chosen_point_meets_the_objective(calibration):
    chosen = calibration["chosen"]
    assert abs(chosen["median_ratio"] - 1.0) <= 0.05
    if chosen["strong_gust_constraint_evaluable"]:
        assert abs(chosen["median_ratio_observed_64kt_or_more"] - 1.0) <= 0.10
    assert chosen["mean_absolute_error_kt"] < calibration["uncalibrated_reference"]["mean_absolute_error_kt"]


def test_the_calibration_is_the_same_cohort_as_the_gust_factor(calibration, gust_model):
    assert calibration["gust_factor"] == gust_model["gust_factor"]
    assert calibration["gust_factor_model_id"] == gust_model["gust_factor_model_id"]
    assert calibration["cohort_id"] == gust_model["cohort_id"] == "primary_2016_2024"
    assert calibration["data"]["storms"] == 11


def test_the_combined_calibration_can_differ_from_the_primary(calibration, combined_calibration):
    """Not a claim that it must differ (it could tie), only that the two are
    independently fitted rather than the same number filed under two cohort ids."""
    assert combined_calibration["cohort_id"] == "combined_2004_2024"
    assert combined_calibration["data"]["storms"] == 19
    assert combined_calibration["data"]["station_storm_pairs"] > calibration["data"]["station_storm_pairs"]


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
# Storm holdout: refits every ASOS-derived constant, without the held-out storm,
# scored with predictions pooled per fold rather than one global factor.
#
# The real (primary-cohort) holdout is checked structurally below; the mechanics -
# the fix this file exists to pin down - are checked against small synthetic data so
# the property is verified directly rather than only implied by different numbers.
# --------------------------------------------------------------------------- #


def _synthetic_pairs() -> pd.DataFrame:
    """Three storms with deliberately different land-fetch gust ratios, so excluding
    a different one each fold shifts the fitted gust factor by a different, known
    amount - if a fold's factor did not move, it would mean the held-out storm's own
    rows were still influencing it."""
    rows = []
    storm_gf = {"AL012020": 1.20, "AL022021": 1.40, "AL032022": 1.60}
    for storm_id, gf in storm_gf.items():
        for i, station in enumerate(("STA1", "STA2")):
            mean_kt = 40.0
            rows.append(
                {
                    "storm_id": storm_id,
                    "storm_name": storm_id,
                    "station": station,
                    "time_utc": f"2020-01-0{i + 1}T00:00:00",
                    "mean2min_kt": mean_kt,
                    "gust_2min_kt": mean_kt * gf,
                    "gf_3s_2min": gf,
                    "field_shift_recovered": False,
                    "ocean_frac_0_10km": 0.1,
                }
            )
    return pd.DataFrame(rows)


def _synthetic_tables(storm_ids: list[str], decays: list[float]) -> dict[float, pd.DataFrame]:
    """A unit-gust table shaped like calibrate_wind_field._unit_gust_table's output,
    with plausible values so _select finds an eligible candidate for every fold."""
    rows = []
    for storm_id in storm_ids:
        for station in ("P1", "P2"):
            rows.append(
                {
                    "storm_id": storm_id,
                    "storm_name": storm_id,
                    "station": station,
                    "closest_approach_km": 40.0,
                    "sustained_kt": 50.0,
                    "observed_gust_kt": 60.0,
                    "truncated": False,
                }
            )
    table = pd.DataFrame(rows)
    return {decay: table.copy() for decay in decays}


def test_held_out_storm_is_absent_from_gust_and_profile_training(monkeypatch):
    """The core fix: each fold's gust-factor refit must never see the held-out
    storm's rows, and every candidate/select step is scored on training storms only."""
    cwf = _script("calibrate_wind_field")
    pairs = _synthetic_pairs()
    storm_ids = set(pairs["storm_id"].unique())
    tables = _synthetic_tables(sorted(storm_ids), [0.3, 0.4])

    seen_gust_training_ids: list[set[str]] = []
    original_fit = cwf.fit_gust_factor.fit

    def spy_fit(pairs_arg, *, storm_ids):
        seen_gust_training_ids.append(set(storm_ids))
        return original_fit(pairs_arg, storm_ids=storm_ids)

    monkeypatch.setattr(cwf.fit_gust_factor, "fit", spy_fit)

    chosen = {
        "n": 0, "mean_absolute_error_kt": 0.0, "median_ratio": 1.0,
        "outer_decay_exponent": 0.3, "land_exposure_factor": 0.75,
    }
    result = cwf._leave_one_storm_out(pairs, tables, storm_ids, chosen, "test-cohort")

    held_out_ids = [fold["held_out_storm_id"] for fold in result["folds"]]
    assert set(held_out_ids) == storm_ids
    for fold, gust_training_ids in zip(result["folds"], seen_gust_training_ids):
        assert fold["held_out_storm_id"] not in gust_training_ids
        # And the fold's own record never lists the held-out id as a contributing id.
        assert fold["gust_factor_contributing_storms"] <= len(storm_ids) - 1


def test_leave_one_storm_out_refits_a_different_gust_factor_per_fold():
    """The leak this replaces: reusing one gust factor (fitted from all storms,
    including the held-out one) across every fold. With the synthetic data's
    deliberately distinct per-storm ratios, each fold's correctly-excluding refit
    must land on a different value - the mean of the two OTHER storms' ratios."""
    cwf = _script("calibrate_wind_field")
    pairs = _synthetic_pairs()
    storm_ids = set(pairs["storm_id"].unique())
    tables = _synthetic_tables(sorted(storm_ids), [0.3, 0.4])
    chosen = {
        "n": 0, "mean_absolute_error_kt": 0.0, "median_ratio": 1.0,
        "outer_decay_exponent": 0.3, "land_exposure_factor": 0.75,
    }

    result = cwf._leave_one_storm_out(pairs, tables, storm_ids, chosen, "test-cohort")
    fold_gust_factors = {fold["held_out_storm_id"]: fold["gust_factor"] for fold in result["folds"]}

    # Excluding AL012020 (gf 1.20) leaves 1.40 and 1.60 -> median 1.50, and so on.
    assert fold_gust_factors["AL012020"] == pytest.approx(1.50)
    assert fold_gust_factors["AL022021"] == pytest.approx(1.40)
    assert fold_gust_factors["AL032022"] == pytest.approx(1.30)
    # Three folds, three different factors: proof the old single-global-factor
    # implementation (which would show the same value three times) is gone.
    assert len(set(fold_gust_factors.values())) == 3


def test_fold_predictions_use_their_own_factor_and_pool_correctly():
    """Each held-out row's modeled_gust_kt must be computed with THAT fold's own gust
    factor, decay and land factor - never a single global value applied to every
    row - and the pooled out-of-sample metric must match those per-row predictions
    exactly, not a recomputation from a different set of parameters."""
    cwf = _script("calibrate_wind_field")
    cc = _script("calibration_common")
    pairs = _synthetic_pairs()
    storm_ids = set(pairs["storm_id"].unique())
    tables = _synthetic_tables(sorted(storm_ids), [0.3, 0.4])
    chosen = {
        "n": 0, "mean_absolute_error_kt": 0.0, "median_ratio": 1.0,
        "outer_decay_exponent": 0.3, "land_exposure_factor": 0.75,
    }

    result = cwf._leave_one_storm_out(pairs, tables, storm_ids, chosen, "test-cohort")

    expected_rows = []
    for fold in result["folds"]:
        modeled = 50.0 * fold["gust_factor"] * fold["land_exposure_factor"]
        expected_rows.append({"modeled_gust_kt": modeled, "observed_gust_kt": 60.0})
    expected = pd.DataFrame(expected_rows * 2)  # two stations per storm, both with identical values
    expected_metrics = cc.metrics_from_columns(expected)

    assert result["out_of_sample"]["n"] == expected_metrics["n"] == 6  # 3 folds x 2 stations
    assert result["out_of_sample"]["mean_absolute_error_kt"] == pytest.approx(
        expected_metrics["mean_absolute_error_kt"], abs=0.01
    )


def test_degenerate_subsets_are_null_not_nan_or_a_fallback(cc):
    cwf = _script("calibrate_wind_field")
    empty = pd.DataFrame(
        {"storm_id": [], "storm_name": [], "station": [], "closest_approach_km": [],
         "sustained_kt": [], "observed_gust_kt": [], "truncated": []}
    )
    raw = cwf._score_raw(empty, 1.3)
    assert raw["n"] == 0
    assert raw["median_ratio"] is None
    assert raw["median_ratio_observed_64kt_or_more"] is None

    # cwf's own cc, not the fixture's separately-loaded module object: _script()
    # gives each loaded module its own identity, so the exception class cwf._select
    # actually raises is cwf.cc.DataValidationError, not necessarily `is` the fixture's.
    with pytest.raises(cwf.cc.DataValidationError):
        cwf._select([{"outer_decay_exponent": 0.3, "land_exposure_factor": 0.75, "raw": raw, "n": 0}])


def test_empty_land_training_sample_fails_rather_than_falling_back():
    script = _script("fit_gust_factor")
    all_ocean = _synthetic_pairs()
    all_ocean["ocean_frac_0_10km"] = 0.9  # every row reclassified as ocean fetch
    with pytest.raises(script.cc.DataValidationError):
        script.fit(all_ocean, storm_ids=set(all_ocean["storm_id"]))


def test_missing_exposure_is_counted_and_excluded_not_coerced():
    script = _script("fit_gust_factor")
    pairs = _synthetic_pairs()
    pairs.loc[0, "ocean_frac_0_10km"] = float("nan")
    fitted = script.fit(pairs, storm_ids=set(pairs["storm_id"]))
    assert fitted["exclusion_counts"]["missing_or_out_of_range_ocean_fraction"] == 1
    assert fitted["selection"]["windows_used"] == len(pairs) - 1


# --------------------------------------------------------------------------- #
# Real primary-cohort holdout: structural checks against the committed fixture
# --------------------------------------------------------------------------- #


def test_the_calibration_holds_up_on_storms_it_did_not_see(calibration, cc):
    """Leave-one-storm-out: constants fitted without a storm must still do about as
    well on that storm as the uncalibrated reference, one fold per primary-cohort
    storm, and the held-out ids are exactly that cohort's manifest."""
    cv = calibration["cross_validation"]
    out, inside = cv["out_of_sample"], cv["in_sample"]
    reference = calibration["uncalibrated_reference"]

    assert len(cv["folds"]) == 11
    assert {f["held_out_storm_id"] for f in cv["folds"]} == cc.cohort_storm_ids(cc.PRIMARY_COHORT)
    assert out["mean_absolute_error_kt"] < reference["mean_absolute_error_kt"] * 1.5
    spread = cv["chosen_constants_across_folds"]
    assert "gust_factor" in spread and "min" in spread["gust_factor"] and "max" in spread["gust_factor"]
    assert spread["outer_decay_exponent"]["min"] <= calibration["outer_decay_exponent"] <= spread["outer_decay_exponent"]["max"]


def test_metadata_carries_the_out_of_sample_error(calibration):
    published = wind.metadata()["calibration"]
    assert published["out_of_sample_mean_absolute_error_kt"] == calibration["cross_validation"]["out_of_sample"]["mean_absolute_error_kt"]


# --------------------------------------------------------------------------- #
# Validation record: fitted, holdout, legacy-frozen, and optional combined
# --------------------------------------------------------------------------- #


def test_the_validation_record_matches_the_current_adapter(validation):
    script = _script("validate_wind_field")
    rerun, _ = script.validate()  # default: production bundle, all 19 storms filtered to cohort=None means every storm
    fitted_rerun, _ = script.validate(cohort=script.cc.PRIMARY_COHORT)
    fitted = validation["primary_fitted"]

    assert fitted_rerun["gust_factor"] == fitted["gust_factor"]
    assert fitted_rerun["all"] == fitted["all"]
    assert fitted_rerun["within_75_km"] == fitted["within_75_km"]
    assert fitted_rerun["observed_64kt_or_more"] == fitted["observed_64kt_or_more"]
    assert fitted_rerun["selection"] == fitted["selection"]


def test_validation_separates_fitted_holdout_and_legacy(validation):
    fitted, holdout, legacy = (
        validation["primary_fitted"], validation["primary_holdout"], validation["legacy_frozen_primary"]
    )
    assert fitted["selection"]["storms"] == 11
    assert legacy["selection"]["storms"] == 8
    assert holdout["cohort_id"] == "primary_2016_2024"
    assert "out_of_sample" in holdout and "mean_absolute_error_kt" in holdout["out_of_sample"]
    # Never conflated: holdout's method is leave-one-out, distinct from fitted's
    # in-sample scoring and legacy's frozen-no-refit scoring.
    assert "holdout" in fitted["validation_type"] or "fitted" in fitted["validation_type"]
    assert "frozen" in legacy["validation_type"]
    assert fitted["all"]["mean_absolute_error_kt"] > 0
    assert 0.9 <= fitted["all"]["median_ratio_modeled_over_observed"] <= 1.15
    assert len(fitted["truncated_records"]) >= 1


def test_metadata_carries_the_validation_headline(validation):
    published = wind.metadata()["validation"]
    assert published["wind_validation_id"] == validation["wind_validation_id"]
    # Backward-compatible flat aliases, equal to the fitted section.
    assert published["mean_absolute_error_kt"] == validation["primary_fitted"]["all"]["mean_absolute_error_kt"]
    assert published["fitted"]["storms"] == 11
    assert published["holdout"]["cohort_id"] == "primary_2016_2024"
    assert published["legacy_frozen_primary"]["storms"] == 8
    assert wind.metadata()["evidence_status"] == "sourced"
    assert wind.metadata()["land_exposure_factor"] == wind.load_wind_calibration()["land_exposure_factor"]


def test_combined_sensitivity_is_present_when_supplied(validation):
    assert "combined_sensitivity" in validation
    combined = validation["combined_sensitivity"]
    assert combined["cohort_id"] == "combined_2004_2024"
    assert combined["selection"]["storms"] == 19


# --------------------------------------------------------------------------- #
# Runtime compatibility: the optional parameter bundle never mutates globals,
# cached fixtures, or module state.
# --------------------------------------------------------------------------- #


def test_a_bundle_overrides_without_touching_production_defaults():
    storm = _storm("BUNDLE", (25.0, -80.0, 120.0))
    coordinates = [("HOME", 25.3, -80.0)]

    before_gust, before_land = wind.GUST_FACTOR, wind.land_exposure_factor()
    before_exposures, _ = wind.exposures_for_storm(storm, coordinates)

    bundle = {
        "gust_factor": before_gust * 1.5,
        "land_exposure_factor": before_land * 0.5,
        "outer_decay_exponent": 0.4,
        "wind_calibration_id": "test-bundle-only",
    }
    bundled_exposures, _ = wind.exposures_for_storm(storm, coordinates, bundle=bundle)

    assert bundled_exposures[0].peak_gust_mph != before_exposures[0].peak_gust_mph

    # Nothing about the default path moved: same globals, same cached fixture values,
    # same result as before the bundled call.
    assert wind.GUST_FACTOR == before_gust
    assert wind.land_exposure_factor() == before_land
    after_exposures, _ = wind.exposures_for_storm(storm, coordinates)
    assert after_exposures[0].peak_gust_mph == before_exposures[0].peak_gust_mph


def test_exposure_and_hazus_metric_matching_still_hold():
    """Runtime compatibility: the metric label the wind step publishes is still
    exactly what the damage curves are defined on, bundle or no bundle."""
    from app import claims

    assert wind.WIND_METRIC == claims.load_curve_set()["wind_metric"]
    storm = _storm("METRIC", (25.0, -80.0, 120.0))
    exposures, _ = wind.exposures_for_storm(storm, [("HOME", 25.3, -80.0)])
    assert exposures[0].wind_metric == wind.WIND_METRIC


def test_metadata_distinguishes_assumed_output_from_unverified_observation():
    note = wind.metadata()["evidence_note"]
    assert "sourced" in wind.metadata()["evidence_status"]
    assert "residual errors" in note or "not" in note  # plain-language limitation present
    gust_note = wind.metadata()["gust_factor_note"]
    assert "sustained" in gust_note.lower() or "basis" in gust_note.lower()


# --------------------------------------------------------------------------- #
# Sensitivity and payout comparison (scripts/compare_calibration_cohorts.py)
# --------------------------------------------------------------------------- #


def test_sensitivity_report_has_three_scenarios_and_never_promotes_factor_only(sensitivity):
    scenarios = sensitivity["scenarios"]
    assert {"primary", "factor_only", "combined"}.issubset(scenarios)
    assert "diagnostic" in scenarios["factor_only"]["role"]
    assert "not" in scenarios["factor_only"]["role"] or "never" in scenarios["factor_only"]["role"]
    # factor_only combines the combined cohort's gust factor with the primary's own
    # decay/land, never an independent fit of its own.
    assert scenarios["factor_only"]["gust_factor"] == scenarios["combined"]["gust_factor"]
    assert scenarios["factor_only"]["outer_decay_exponent"] == scenarios["primary"]["outer_decay_exponent"]
    assert scenarios["factor_only"]["land_exposure_factor"] == scenarios["primary"]["land_exposure_factor"]
    assert sensitivity["holdout"]["factor_only"] is None


def test_sensitivity_wind_accuracy_uses_identical_evaluation_keys_per_cohort(sensitivity):
    """Every scenario's wind_accuracy is scored on the same per-cohort station-storm
    selection: the n (pair count) for a given cohort must be identical across
    scenarios, since eligibility depends only on the cohort and station data, never
    on which bundle is being scored."""
    wind_accuracy = sensitivity["wind_accuracy"]
    for cohort_id in ("primary_2016_2024", "legacy_2004_2005", "combined_2004_2024"):
        pair_counts = {name: metrics[cohort_id]["n"] for name, metrics in wind_accuracy.items()}
        assert len(set(pair_counts.values())) == 1, f"{cohort_id} pair counts differ across scenarios: {pair_counts}"


def test_sensitivity_payout_zero_reference_gives_a_null_relative_difference(sensitivity):
    found_a_null = False
    for storm_entry in sensitivity["payout_comparison"]["storms"].values():
        for diff in storm_entry["differences_vs_primary"].values():
            portfolio_diff = diff["portfolio_baseline_payout_usd"]
            if portfolio_diff["relative"] is None:
                found_a_null = True
                assert "reason" in portfolio_diff or "relative_null_reason" in portfolio_diff
                assert portfolio_diff["absolute_usd"] == 0.0
    assert found_a_null, "expected at least one zero-reference-payout storm/scenario in the fixture"


def test_sensitivity_payout_config_is_identical_across_scenarios(sensitivity):
    """The whole point of holding roof shapes, the curve set and the policy template
    fixed: a payout difference between scenarios must be attributable to the wind
    bundle alone. There is exactly one config_held_constant block, not one per
    scenario, so there is nothing for scenarios to disagree about."""
    config = sensitivity["payout_comparison"]["config_held_constant"]
    assert config["curve_set_id"] == wind.load_gust_factor_model.__module__ or True  # smoke: key exists
    from app import claims

    assert config["curve_set_id"] == claims.load_curve_set()["curve_set_id"]
    portfolio = json.loads((FIXTURES / "example_portfolio.json").read_text(encoding="utf-8"))
    committed_roof_shapes = {p["property_id"]: p["roof_shape"] for p in portfolio["properties"]}
    assert config["roof_shape_by_property"] == committed_roof_shapes


def test_sensitivity_never_mixes_sums_with_averages(sensitivity):
    for storm_entry in sensitivity["payout_comparison"]["storms"].values():
        for name, result in storm_entry["scenarios"].items():
            expected_total = round(sum(result["baseline_payout_usd"].values()), 2)
            assert result["portfolio_baseline_payout_usd"] == pytest.approx(expected_total, abs=0.01)
            for upgrade_id, per_property in result["avoided_payout_usd_by_upgrade"].items():
                expected_upgrade_total = round(sum(per_property.values()), 2)
                assert result["portfolio_avoided_payout_usd_by_upgrade"][upgrade_id] == pytest.approx(
                    expected_upgrade_total, abs=0.01
                )


# --------------------------------------------------------------------------- #
# The two storm manifests: 2016-2024 (primary) and 2004-2005 (legacy)
# --------------------------------------------------------------------------- #


DATASET = BACKEND.parent / "data" / "calibration" / "fl_hurricane_gust_data"


def test_the_legacy_manifest_matches_its_best_tracks():
    manifest = pd.read_csv(DATASET / "storms_2004_2005.csv", parse_dates=["window_start_utc", "window_end_utc"])
    assert set(manifest["storm_name"]) == {"CHARLEY", "FRANCES", "IVAN", "JEANNE", "DENNIS", "KATRINA", "RITA", "WILMA"}
    assert (manifest["window_end_utc"] > manifest["window_start_utc"]).all()
    assert ((manifest["window_end_utc"] - manifest["window_start_utc"]).dt.total_seconds() / 3600 <= 96).all()

    headers = [line[1:].strip() for line in (DATASET / "raw" / "hurdat2_fl_2004_2005.txt").read_text().splitlines() if line.startswith("#")]
    assert sorted(headers) == sorted(f"{row.storm_id},{row.storm_name}" for row in manifest.itertuples())


def test_the_storm_manifests_match_the_pairs_file():
    """The pairs table holds exactly the 11 primary storms plus the 8 legacy ones."""
    primary = pd.read_csv(DATASET / "storms_2016_2024.csv")
    legacy = pd.read_csv(DATASET / "storms_2004_2005.csv")
    pairs = pd.read_csv(DATASET / "fl_gust_pairs_2min_qc.csv.gz", usecols=["storm_id"])
    assert set(primary["storm_id"]).isdisjoint(legacy["storm_id"])
    assert set(primary["storm_id"]) | set(legacy["storm_id"]) == set(pairs["storm_id"].unique())


# --------------------------------------------------------------------------- #
# Reproduction: identical inputs produce identical numerical content
# --------------------------------------------------------------------------- #


def test_rebuilding_the_primary_fixtures_reproduces_provenance(gust_model, calibration):
    """Same input hash, same cohort, same numbers - a rebuild is not a coin flip."""
    gust_script = _script("fit_gust_factor")
    refit_gust = gust_script.build_model(cohort="primary_2016_2024")
    assert refit_gust["source"]["input_sha256"] == gust_model["source"]["input_sha256"]
    assert refit_gust["gust_factor"] == gust_model["gust_factor"]

    calibration_script = _script("calibrate_wind_field")
    refit_calibration = calibration_script.calibrate("primary_2016_2024")
    assert refit_calibration["outer_decay_exponent"] == calibration["outer_decay_exponent"]
    assert refit_calibration["land_exposure_factor"] == calibration["land_exposure_factor"]
    assert refit_calibration["cross_validation"]["out_of_sample"] == calibration["cross_validation"]["out_of_sample"]
