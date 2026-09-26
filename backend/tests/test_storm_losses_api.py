"""The storm-loss endpoints and the provisional wind field behind them.

These run against the shipped fixtures, so they also serve as a regression check on
the curve set and the storm catalog: if someone re-imports a catalog without the Miami
landfall, or ships curves that no longer cover 170 mph, these fail loudly here rather
than quietly in the client.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import json
import pathlib

import pytest
from starlette.testclient import TestClient

from app import wind
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
    assert example["evidence_status"] == "assumed"
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
    assert metadata["wind_model"]["evidence_status"] == "assumed"
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
        assert curve["evidence_status"] == "assumed"
        assert curve["source_note"]
        assert curve["wind_metric"] == curves["wind_metric"]
    assert "roof_straps" not in curves["eligible_upgrades_by_class"]["post_fbc_2002"]
    assert curves["policy_template"]["deductible"]["percent"] == 0.05


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
    # 140 mph is a declared point on the curve: 0.1448 x 500,000.
    assert body["rows"][0]["baseline_damage_usd"] == pytest.approx(72_400, abs=1)
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


def test_peak_gust_is_the_worst_over_the_track_not_the_value_at_landfall():
    """A property can see its worst wind while the centre is still offshore."""
    track = [
        {"step": 0, "timestamp": "t0", "latitude": 25.0, "longitude": -80.0,
         "max_wind_kt": 130.0, "category": "4", "is_over_land": False},
        {"step": 1, "timestamp": "t1", "latitude": 27.0, "longitude": -82.0,
         "max_wind_kt": 60.0, "category": "TS", "is_over_land": True},
    ]
    gust, step = wind.peak_gust_mph(25.0, -80.0, track)

    assert step["step"] == 0
    assert gust == pytest.approx(130.0 * wind.KT_TO_MPH * wind.GUST_FACTOR, rel=1e-6)


def test_distant_property_is_floored_to_zero_exposure():
    track = [
        {"step": 0, "timestamp": "t0", "latitude": 25.0, "longitude": -80.0,
         "max_wind_kt": 130.0, "category": "4", "is_over_land": False}
    ]
    gust, step = wind.peak_gust_mph(45.0, -70.0, track)

    assert gust == 0.0
    # Still reports where the centre was, so the zero can be checked.
    assert step is not None and step["distance_km"] > 1000


def test_every_property_gets_an_exposure_row_including_zeros():
    storm = wind.storm_by_id(EXAMPLE_STORM)
    exposures, detail = wind.exposures_for_storm(
        storm, [("P001", 25.7617, -80.1918), ("PFAR", 48.0, -60.0)]
    )

    assert len(exposures) == 2
    assert len(detail) == 2
    assert {e.property_id for e in exposures} == {"P001", "PFAR"}
    assert all(e.wind_metric == wind.WIND_METRIC for e in exposures)


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
