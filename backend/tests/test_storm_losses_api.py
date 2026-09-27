"""The storm-loss endpoints and the wind field (the wind_field package) behind them.

These run against the shipped fixtures, so they also serve as a regression check on
the curve set and the storm catalog: if someone re-imports a catalog without the Miami
landfall, or ships curves that no longer cover 170 mph, these fail loudly here rather
than quietly in the client.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import json
import math
import pathlib
from datetime import datetime, timedelta

import pytest
import wind_field
from starlette.testclient import TestClient
from wind_field import WindFieldConfig, haversine_distance_km
from wind_field.profile import sustained_wind_profile_kt
from wind_field.schema import DEMO_STORM_PARAMETERS as SIZE

from app import claims, wind
from app.main import app

client = TestClient(app)

EXAMPLE_STORM = "SYN0155"
MIAMI = "P001"
JACKSONVILLE = "P006"
POST_FBC = "P002"


@pytest.fixture
def example() -> dict:
    response = client.get("/api/v1/storm-losses/example")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# The example run
# --------------------------------------------------------------------------- #


def test_example_answers_with_no_request_body(example):
    assert example["schema_version"] == "1.1"
    assert example["storm_ids"] == [EXAMPLE_STORM]
    assert len(example["property_ids"]) == 10
    assert example["evidence_status"] == "sourced", "wind and curves are both sourced now"
    assert example["rows"]


def test_example_is_complete_across_declared_options(example):
    """Every declared property/upgrade pair appears exactly once per storm."""
    expected = {
        (storm_id, option["property_id"], upgrade_id)
        for storm_id in example["storm_ids"]
        for option in example["eligible_options"]
        for upgrade_id in option["upgrade_ids"]
    }
    actual = [(r["storm_id"], r["property_id"], r["upgrade_id"]) for r in example["rows"]]

    assert len(actual) == len(set(actual)), "duplicate row in the response"
    assert set(actual) == expected


def test_storm_that_misses_a_home_returns_an_explicit_zero_row(example):
    """Jacksonville is ~500 km from this landfall: a zero row, not an absent one."""
    rows = [r for r in example["rows"] if r["property_id"] == JACKSONVILLE]

    assert rows, "no row at all for the property the storm missed"
    for row in rows:
        assert row["baseline_damage_usd"] == 0
        assert row["baseline_payout_usd"] == 0
        assert row["avoided_payout_usd"] == 0


def test_storm_that_hits_a_home_produces_a_payout(example):
    rows = [r for r in example["rows"] if r["property_id"] == MIAMI]

    assert rows
    for row in rows:
        assert row["peak_gust_mph"] > 140
        assert row["baseline_payout_usd"] > 0
        assert row["upgraded_payout_usd"] < row["baseline_payout_usd"]
        assert row["avoided_payout_usd"] > 0


def test_baseline_never_differs_between_upgrade_rows(example):
    """The brief requires this and it is the easiest thing to break."""
    seen: dict[tuple[str, str], float] = {}
    for row in example["rows"]:
        key = (row["storm_id"], row["property_id"])
        if key in seen:
            assert seen[key] == row["baseline_payout_usd"]
        seen[key] = row["baseline_payout_usd"]


def test_code_compliant_home_is_not_offered_a_roof_strap_retrofit(example):
    """post_fbc_2002 has no roof_straps curve, so it must not be an eligible option."""
    options = {o["property_id"]: o["upgrade_ids"] for o in example["eligible_options"]}

    assert "roof_straps" not in options[POST_FBC]
    assert "shutters" in options[POST_FBC]
    assert not any(
        r["property_id"] == POST_FBC and r["upgrade_id"] == "roof_straps"
        for r in example["rows"]
    )


def test_run_publishes_the_provenance_a_reader_needs(example):
    metadata = example["metadata"]

    assert metadata["curve_ids"]
    assert metadata["curve_source_notes"]
    assert metadata["sampling_description"]
    assert metadata["curve_wind_metric"] == metadata["wind_model"]["wind_metric"]
    assert metadata["wind_model"]["evidence_status"] == "sourced"
    assert example["evidence_status"] == "sourced", "the curves are Hazus, the wind is calibrated"
    assert metadata["curve_provenance"]["class_mapping"], "the Hazus mapping must travel with the run"
    assert metadata["wind_model"]["reference_height_m"] == 10
    assert metadata["wind_model"]["gust_factor"]
    assert metadata["policy_basis"]["deductible_percent"] == 0.05
    assert "reinsurance" in metadata["result_basis"]


def test_zero_rows_are_auditable_as_misses(example):
    """A zero row is only trustworthy if you can see how far away the storm passed.

    The gust itself is not zero - the property felt a breeze - it is simply well below
    the 75 mph where the curves first show damage. Reporting the real gust next to the
    closest approach is what separates a modeled miss from a dropped row.
    """
    detail = {
        (d["storm_id"], d["property_id"]): d
        for d in example["metadata"]["wind_exposure_detail"]
    }
    entry = detail[(EXAMPLE_STORM, JACKSONVILLE)]

    assert entry["peak_gust_mph"] < 75
    assert entry["closest_approach"]["distance_km"] > 150


def test_assumptions_state_the_scope_limits(example):
    text = " ".join(example["assumptions"]).lower()

    assert "before reinsurance" in text
    assert "assumed" in text
    assert "deductible resets" in text


# --------------------------------------------------------------------------- #
# Catalog and curves
# --------------------------------------------------------------------------- #


def test_catalog_exposes_tracks_a_client_can_animate():
    catalog = client.get("/api/v1/storm-catalog").json()

    assert catalog["catalog_id"]
    assert EXAMPLE_STORM in catalog["storm_ids"]
    storm = next(s for s in catalog["storms"] if s["storm_id"] == EXAMPLE_STORM)
    assert len(storm["track"]) > 5
    steps = [point["step"] for point in storm["track"]]
    assert steps == sorted(steps), "track points must be in time order"
    for point in storm["track"]:
        assert {"timestamp", "latitude", "longitude", "max_wind_kt", "category"} <= point.keys()


def test_catalog_says_its_wind_is_centre_wind_not_property_wind():
    catalog = client.get("/api/v1/storm-catalog").json()

    assert catalog["wind_metric"] == "max_wind_kt"
    assert "CENTRE" in catalog["wind_metric_note"]
    assert "not a probabilistic sample" in catalog["completeness_warning"]


def test_curves_endpoint_publishes_evidence_status_per_curve():
    curves = client.get("/api/v1/damage-curves").json()

    assert curves["curves"]
    for curve in curves["curves"]:
        assert curve["evidence_status"] == "sourced"
        assert curve["source_note"]
        assert curve["wind_metric"] == curves["wind_metric"]
        assert curve["roof_shape"] in ("gable", "hip", "blended")
    assert {c["roof_shape"] for c in curves["curves"]} == {"gable", "hip", "blended"}
    assert "roof_straps" not in curves["eligible_upgrades_by_class"]["post_fbc_2002"]
    assert curves["policy_template"]["deductible"]["percent"] == 0.05


def test_demo_roof_shapes_are_reproducible_from_the_seed():
    """example_portfolio.json's roof_shape values are exactly what
    scripts/assign_demo_roof_shapes.py's fixed seed derives, and every value is a
    real roof shape - never invented data quietly upgraded past "not measured"."""
    import importlib.util

    backend = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "assign_demo_roof_shapes", backend / "scripts" / "assign_demo_roof_shapes.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    portfolio_path = backend / "app" / "fixtures" / "example_portfolio.json"
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    expected = script.assign_roof_shapes(portfolio["properties"])

    assert portfolio["properties"], "no demo properties to check"
    for prop in portfolio["properties"]:
        assert prop["roof_shape"] == expected[prop["property_id"]]
        assert prop["roof_shape"] in ("gable", "hip")
    assert "roof_shape" in portfolio["missing_input"], "the placeholder must say it is not measured"


# --------------------------------------------------------------------------- #
# The POST contract
# --------------------------------------------------------------------------- #


def test_post_accepts_the_frontend_property_shape():
    """id and value, straight from frontend/src/data/properties.ts."""
    response = client.post(
        "/api/v1/storm-losses",
        json={
            "storm_ids": [EXAMPLE_STORM],
            "run_id": "test-post",
            "properties": [
                {"id": 1, "value": 850000, "latitude": 25.7617, "longitude": -80.1918}
            ],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["property_ids"] == ["1"]
    assert body["rows"][0]["baseline_payout_usd"] > 0


def test_post_accepts_supplied_wind_exposures():
    response = client.post(
        "/api/v1/storm-losses",
        json={
            "storm_ids": [EXAMPLE_STORM],
            "properties": [
                {
                    "property_id": "P001",
                    "replacement_cost_usd": 500000,
                    "vulnerability_class": "pre_fbc_2002",
                }
            ],
            "wind_exposures": [
                {
                    "storm_id": EXAMPLE_STORM,
                    "property_id": "P001",
                    "peak_gust_mph": 140,
                    "wind_metric": wind.WIND_METRIC,
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # 140 mph is a declared point on the shipped curve: its fraction x 500,000. No
    # roof_shape was declared, so this property falls back to the blended curve.
    curve = claims.load_curve_set()["curves"][("pre_fbc_2002", "baseline", "blended")]
    fraction = dict(curve.points)[140.0]
    assert body["rows"][0]["baseline_damage_usd"] == pytest.approx(fraction * 500_000, abs=1)
    assert body["metadata"]["wind_model"]["evidence_status"] == "caller-declared"


def test_supplied_exposure_with_the_wrong_metric_is_rejected():
    response = client.post(
        "/api/v1/storm-losses",
        json={
            "storm_ids": [EXAMPLE_STORM],
            "properties": [{"property_id": "P001", "replacement_cost_usd": 500000}],
            "wind_exposures": [
                {
                    "storm_id": EXAMPLE_STORM,
                    "property_id": "P001",
                    "peak_gust_mph": 140,
                    "wind_metric": "1min_sustained_kt",
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "UnitMismatchError"


def test_unknown_storm_id_is_a_404_listing_what_exists():
    response = client.post(
        "/api/v1/storm-losses",
        json={
            "storm_ids": ["SYN9999"],
            "properties": [
                {"property_id": "P001", "replacement_cost_usd": 500000,
                 "latitude": 25.76, "longitude": -80.19}
            ],
        },
    )

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["unknownStormIds"] == ["SYN9999"]
    assert EXAMPLE_STORM in detail["available"]


def test_missing_coordinates_without_exposures_is_a_422():
    response = client.post(
        "/api/v1/storm-losses",
        json={
            "storm_ids": [EXAMPLE_STORM],
            "properties": [{"property_id": "P001", "replacement_cost_usd": 500000}],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["propertyIds"] == ["P001"]


def test_unknown_vulnerability_class_is_a_422_naming_the_class():
    response = client.post(
        "/api/v1/storm-losses",
        json={
            "storm_ids": [EXAMPLE_STORM],
            "properties": [
                {
                    "property_id": "P001",
                    "replacement_cost_usd": 500000,
                    "vulnerability_class": "adobe_hut",
                    "latitude": 25.76,
                    "longitude": -80.19,
                }
            ],
        },
    )

    assert response.status_code == 422
    assert "adobe_hut" in response.json()["detail"]["message"]


# --------------------------------------------------------------------------- #
# The wind field itself
# --------------------------------------------------------------------------- #


def _storm(*points):
    """A test storm on the catalog's 6-hour steps, from (latitude, longitude, max_wind_kt)."""
    start = datetime(2026, 9, 1)
    return {
        "storm_id": "TEST",
        "track": [
            {"step": step, "timestamp": (start + timedelta(hours=6 * step)).strftime("%Y-%m-%d %H:%M:%S"),
             "latitude": latitude, "longitude": longitude, "max_wind_kt": knots,
             "category": "4", "is_over_land": False}
            for step, (latitude, longitude, knots) in enumerate(points)
        ],
    }


def _expected_gust(distance_km, centre_wind_kt, storm):
    """The gust wind_field's profile gives at this distance, the independent check,
    using the size the storm-size model assigns to this storm."""
    size = wind.storm_parameters(storm)
    sustained = sustained_wind_profile_kt(
        distance_km, centre_wind_kt, size["rmw_km"], size["outer_decay_exponent"],
        size["taper_start_km"], size["cutoff_km"],
    )
    return sustained * wind.KT_TO_MPH * wind.land_exposure_factor() * wind.GUST_FACTOR


def _km_east(latitude, longitude, km):
    return longitude + km / (111.195 * math.cos(math.radians(latitude)))


def test_peak_gust_is_the_worst_over_the_track_not_the_value_at_landfall():
    """A property can see its worst wind while the centre is still offshore.

    A 130 kt centre passes 40 km west of the property, then weakens to 60 kt and makes
    landfall far to the north-west: the peak comes from the offshore hours.
    """
    storm = _storm((25.0, -80.0, 130.0), (27.0, -82.0, 60.0))
    home = (25.0, _km_east(25.0, -80.0, 40.0))
    exposures, detail = wind.exposures_for_storm(storm, [("HOME", *home)])

    distance = haversine_distance_km(*home, 25.0, -80.0)
    assert exposures[0].peak_gust_mph == pytest.approx(_expected_gust(distance, 130.0, storm), abs=0.01)
    assert detail[0]["peak_time_utc"] == "2026-09-01T00:00:00"


def test_wind_peaks_at_the_radius_of_maximum_wind_not_in_the_eye():
    storm = _storm((25.0, -80.0, 130.0))
    eyewall = (25.0, _km_east(25.0, -80.0, wind.storm_parameters(storm)["rmw_km"]))
    exposures, _ = wind.exposures_for_storm(storm, [("EYE", 25.0, -80.0), ("EYEWALL", *eyewall)])
    gust = {exposure.property_id: exposure.peak_gust_mph for exposure in exposures}

    assert gust["EYE"] == 0.0
    assert gust["EYEWALL"] == pytest.approx(
        _expected_gust(haversine_distance_km(*eyewall, 25.0, -80.0), 130.0, storm), abs=0.01
    )
    assert gust["EYEWALL"] > 0.99 * 130.0 * wind.KT_TO_MPH * wind.land_exposure_factor() * wind.GUST_FACTOR


def test_distant_property_is_floored_to_zero_exposure():
    exposures, detail = wind.exposures_for_storm(_storm((25.0, -80.0, 130.0)), [("FAR", 45.0, -70.0)])

    assert exposures[0].peak_gust_mph == 0.0
    # Still reports how far away the centre passed, so the zero can be checked.
    assert detail[0]["closest_approach"]["distance_km"] > 1000
    assert detail[0]["peak_time_utc"] is None


def test_a_gap_in_the_stored_track_is_modeled_in_stretches_not_bridged():
    """The storm's closest pass falls in the gap, so it is missing, not invented."""
    full = _storm((25.0, -78.0, 130.0), (25.0, -79.0, 130.0), (25.0, -80.0, 130.0))
    gapped = {"storm_id": "TEST", "track": [full["track"][0], full["track"][2]]}
    home = [("HOME", 25.27, -79.0)]

    full_gust = wind.exposures_for_storm(full, home)[0][0].peak_gust_mph
    gapped_gust = wind.exposures_for_storm(gapped, home)[0][0].peak_gust_mph

    assert 0 < gapped_gust < full_gust
    assert wind.track_gaps(full) == []
    (warning,) = wind.track_gaps(gapped)
    assert "12 hours" in warning and "step 0 to step 2" in warning


def test_every_property_gets_an_exposure_row_including_zeros():
    storm = wind.storm_by_id(EXAMPLE_STORM)
    exposures, detail = wind.exposures_for_storm(
        storm, [("P001", 25.7617, -80.1918), ("PFAR", 48.0, -60.0)]
    )

    assert len(exposures) == 2
    assert len(detail) == 2
    assert {e.property_id for e in exposures} == {"P001", "PFAR"}
    assert all(e.wind_metric == wind.WIND_METRIC for e in exposures)


def test_the_example_run_warns_about_its_storm_track_gap(example):
    """SYN0155's stored track skips step 40; the response says so."""
    assert any(
        warning.startswith(f"{EXAMPLE_STORM}: the stored track jumps 12 hours")
        for warning in example["warnings"]
    )


def test_wind_model_is_wind_field_with_the_agreed_gust_factor(example):
    wind_model = example["metadata"]["wind_model"]

    assert wind_model["model"] == "wind_field"
    assert wind_model["package_version"] == wind_field.__version__
    assert wind_model["gust_factor"] == wind.GUST_FACTOR == wind.load_gust_factor_model()["gust_factor"]
    assert wind_model["evidence_status"] == "sourced"
    assert wind_model["validation"]["wind_validation_id"] == "fl-asos-hurricane-peaks-v2"
    assert wind_model["storm_parameters"]["parameter_status"] == "sourced"
    assert wind_model["storm_parameters"]["storm_size_model_id"] == "hurdat2-radii-fit-v1"
    assert example["metadata"]["storm_size"][0]["storm_id"] == "SYN0155"


def test_wind_field_labels_its_output_with_the_curves_metric():
    config = WindFieldConfig(
        run_id="test", catalog_id="test",
        gust_factor=wind.GUST_FACTOR, gust_duration_seconds=wind.GUST_DURATION_SECONDS,
    )
    assert config.wind_metric == wind.WIND_METRIC == claims.load_curve_set()["wind_metric"]


@pytest.mark.parametrize("storm_id", wind.load_catalog()["storm_ids"])
def test_every_catalog_storm_prices_the_frontend_portfolio(storm_id):
    """Exactly what the dashboard sends: its Property shape, one storm at a time."""
    portfolio_path = pathlib.Path(__file__).resolve().parents[1] / "app" / "fixtures" / "example_portfolio.json"
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))["properties"]
    properties = [
        {"id": index + 1, "address": entry["address"], "city": entry["city"],
         "county": entry["county"], "latitude": entry["latitude"],
         "longitude": entry["longitude"], "value": entry["replacement_cost_usd"]}
        for index, entry in enumerate(portfolio)
    ]
    response = client.post(
        "/api/v1/storm-losses", json={"properties": properties, "storm_ids": [storm_id]}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["property_ids"]) == len(portfolio)
    assert body["rows"]


def test_a_property_sent_twice_is_a_422_not_a_500():
    home = {"property_id": "P001", "replacement_cost_usd": 500000,
            "latitude": 25.76, "longitude": -80.19}
    response = client.post(
        "/api/v1/storm-losses", json={"storm_ids": [EXAMPLE_STORM], "properties": [home, home]}
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "WindFieldInputError"
    assert "P001" in response.json()["detail"]["message"]


# --------------------------------------------------------------------------- #
# The published sample
# --------------------------------------------------------------------------- #


def test_published_sample_matches_the_live_endpoint(example):
    """The committed sample is what the consuming developer builds against.

    The example run is deterministic - fixed run id, fixed fixtures - so the sample can
    be compared exactly. If this fails, regenerate it rather than editing it by hand:

        python -c "import json,pathlib; from starlette.testclient import TestClient; \
from app.main import app; pathlib.Path('app/fixtures/sample_storm_losses_response.json') \
.write_text(json.dumps(TestClient(app).get('/api/v1/storm-losses/example').json(), \
indent=2) + chr(10), encoding='utf-8')"
    """
    sample_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app"
        / "fixtures"
        / "sample_storm_losses_response.json"
    )
    sample = json.loads(sample_path.read_text(encoding="utf-8"))

    assert sample == example, "sample_storm_losses_response.json is stale"
