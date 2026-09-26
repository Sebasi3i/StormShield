"""Live storm generation: draw a hurricane that hits Florida and price it.

These are slower than the rest of the suite - each one asks the simulator for real
storms, roughly a second apiece - so there are deliberately few of them and they share
seeds where they can. The fast, exhaustive coverage of the pricing engine lives in
test_claims.py and test_storm_losses_api.py against the fixed catalog.

Two things they exist to catch. First, that every storm returned really is a major
hurricane that reaches Florida: an earlier version of the Florida test used a bounding
box and happily returned landfalls in Yucatan, Cuba and Louisiana. Second, that the
response always says how many storms had to be generated, because that count is the
rarity of the event and the only honest substitute for a probability.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import json
import pathlib

import pytest
from starlette.testclient import TestClient

from app import storms, wind
from app.main import app

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not storms.HURDAT2.exists(),
    reason=f"vendored HURDAT2 record missing at {storms.HURDAT2}",
)


@pytest.fixture(scope="module")
def portfolio() -> list[dict]:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app"
        / "fixtures"
        / "example_portfolio.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))["properties"]


@pytest.fixture(scope="module")
def request_properties(portfolio) -> list[dict]:
    return [
        {
            "property_id": entry["property_id"],
            "replacement_cost_usd": entry["replacement_cost_usd"],
            "vulnerability_class": entry["vulnerability_class"],
            "latitude": entry["latitude"],
            "longitude": entry["longitude"],
        }
        for entry in portfolio
    ]


@pytest.fixture(scope="module")
def run(request_properties) -> dict:
    """One seeded run, shared by most assertions to keep the suite quick."""
    response = client.post(
        "/api/v1/simulate-storm", json={"properties": request_properties, "seed": 11}
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# What comes back
# --------------------------------------------------------------------------- #


def test_returns_a_major_hurricane_that_hits_florida(run):
    """The core invariant. If the Florida test regresses, this is what catches it."""
    storm = run["metadata"]["storm"]

    assert storm["category"] >= 3
    assert storm["peak_wind_kt"] >= storms.CATEGORY_MIN_KT[3]
    assert storms.strikes_florida(storm["track"]), (
        f"{storm['storm_id']} was returned but does not strike Florida; "
        f"landfall reported at {storm['landfall_lat']}, {storm['landfall_lon']}"
    )


def test_track_is_ordered_and_animatable(run):
    track = run["metadata"]["storm"]["track"]

    assert len(track) > 1
    steps = [point["step"] for point in track]
    assert steps == sorted(steps)
    for point in track:
        assert {"timestamp", "latitude", "longitude", "max_wind_kt", "category"} <= point.keys()


def test_reports_how_many_storms_it_had_to_generate(run):
    """The count is the rarity. Without it the payout cannot be interpreted."""
    generation = run["metadata"]["storm_generation"]

    assert generation["storms_generated"] >= 1
    assert generation["majors_considered"] >= 1
    assert generation["min_category"] == 3
    assert "1-in-" in generation["interpretation"]
    assert "not an expected loss" in generation["interpretation"]
    assert "NOT selected for" in generation["interpretation"]


def test_every_property_and_upgrade_is_priced(run):
    expected = {
        (option["property_id"], upgrade)
        for option in run["eligible_options"]
        for upgrade in option["upgrade_ids"]
    }
    actual = [(row["property_id"], row["upgrade_id"]) for row in run["rows"]]

    assert len(actual) == len(set(actual)), "duplicate row"
    assert set(actual) == expected
    assert len(run["storm_ids"]) == 1


def test_rows_carry_damage_as_well_as_payout(run):
    """A client bound only to payout shows $0 while a Cat 4 is on screen.

    About 60% of these storms damage a home and only ~14% clear a 5% deductible, so both
    numbers have to be present for the UI to have anything to say.
    """
    for row in run["rows"]:
        assert "baseline_damage_usd" in row
        assert "baseline_payout_usd" in row
        assert row["baseline_damage_usd"] >= row["baseline_payout_usd"]


def test_gusts_match_the_track_that_was_returned(run, portfolio):
    """Recompute one gust from the published track; the two must agree.

    Guards the seam between the storm a client animates and the numbers beside it.
    """
    track = run["metadata"]["storm"]["track"]
    row = max(run["rows"], key=lambda r: r["peak_gust_mph"])
    entry = next(p for p in portfolio if p["property_id"] == row["property_id"])

    recomputed, _ = wind.peak_gust_mph(entry["latitude"], entry["longitude"], track)

    assert recomputed == pytest.approx(row["peak_gust_mph"], abs=0.05)


def test_wind_model_is_still_labelled_provisional(run):
    """Adryel owns the wind field. Until he delivers, this must not look settled."""
    metadata = run["metadata"]

    assert metadata["wind_model"]["evidence_status"] == "assumed"
    assert metadata["wind_model"]["wind_metric"] == wind.WIND_METRIC
    assert "ASSUMED" in metadata["wind_model"]["gust_factor_note"]
    assert run["evidence_status"] == "assumed"


def test_simulator_provenance_travels_with_the_result(run):
    simulator = run["metadata"]["simulator"]

    assert simulator["package"] == "hurricane_simulator"
    assert "HURDAT2" in simulator["boundary"] or "CENTRE" in simulator["boundary"]
    assert "average annual loss" in simulator["not_a_rate"]


def test_no_field_attaches_an_annual_rate(run):
    """The prose may warn about rates; no field may carry one."""

    def keys_of(node) -> set[str]:
        if isinstance(node, dict):
            found = set(node)
            for value in node.values():
                found |= keys_of(value)
            return found
        if isinstance(node, list):
            found: set[str] = set()
            for value in node:
                found |= keys_of(value)
            return found
        return set()

    forbidden = {
        "annual_probability",
        "annual_rate",
        "return_period",
        "exceedance_probability",
        "average_annual_loss",
        "aal",
    }
    assert not forbidden & {key.lower() for key in keys_of(run)}


# --------------------------------------------------------------------------- #
# Generation behaviour
# --------------------------------------------------------------------------- #


def test_seed_makes_a_run_reproducible(request_properties):
    first = client.post(
        "/api/v1/simulate-storm", json={"properties": request_properties, "seed": 4}
    ).json()
    second = client.post(
        "/api/v1/simulate-storm", json={"properties": request_properties, "seed": 4}
    ).json()

    assert first["metadata"]["storm"] == second["metadata"]["storm"]
    assert first["rows"] == second["rows"]


def test_different_seeds_give_different_storms(request_properties):
    tracks = []
    for seed in (1, 2, 3):
        body = client.post(
            "/api/v1/simulate-storm", json={"properties": request_properties, "seed": seed}
        ).json()
        storm = body["metadata"]["storm"]
        tracks.append((storm["peak_wind_kt"], tuple(p["latitude"] for p in storm["track"])))

    assert len(set(tracks)) > 1, "three seeds produced identical storms"


def test_a_higher_category_floor_is_respected(request_properties):
    body = client.post(
        "/api/v1/simulate-storm",
        json={"properties": request_properties, "min_category": 4, "seed": 9},
    ).json()

    assert body["metadata"]["storm"]["category"] >= 4
    assert body["metadata"]["storm_generation"]["min_category"] == 4


def test_generator_is_callable_directly_without_http():
    """The generator is a plain function, so it can be used in a batch job or notebook."""
    storm, generation = storms.generate_florida_storm(min_category=3, seed=2)

    assert storm["category"] >= 3
    assert storms.strikes_florida(storm["track"])
    assert generation["storms_generated"] % storms.DRAW_BATCH == 0


def test_florida_test_rejects_a_storm_that_only_crosses_the_open_gulf():
    """The regression that motivated the current Florida test.

    25.0N 85.0W is inside Florida's bounding box and about 340 km west of Naples - open
    water. A box-based test accepted it; this one must not.
    """
    gulf_only = [
        {
            "step": 0,
            "timestamp": "2026-09-01 00:00:00",
            "latitude": 25.0,
            "longitude": -85.0,
            "max_wind_kt": 130.0,
            "category": "4",
            "is_over_land": False,
        }
    ]
    assert not storms.strikes_florida(gulf_only)


def test_florida_test_accepts_a_landfalling_storm():
    ashore = [
        {
            "step": 0,
            "timestamp": "2026-09-01 00:00:00",
            "latitude": 25.95,
            "longitude": -80.25,
            "max_wind_kt": 130.0,
            "category": "4",
            "is_over_land": True,
        }
    ]
    assert storms.strikes_florida(ashore)


def test_tropical_storm_over_florida_is_not_a_strike():
    """Below hurricane force, crossing the coast is not the event we price."""
    weak = [
        {
            "step": 0,
            "timestamp": "2026-09-01 00:00:00",
            "latitude": 25.95,
            "longitude": -80.25,
            "max_wind_kt": 45.0,
            "category": "TS",
            "is_over_land": True,
        }
    ]
    assert not storms.strikes_florida(weak)


# --------------------------------------------------------------------------- #
# Rejections
# --------------------------------------------------------------------------- #


def test_missing_coordinates_are_rejected(request_properties):
    stripped = [
        {k: v for k, v in prop.items() if k not in ("latitude", "longitude")}
        for prop in request_properties
    ]
    response = client.post("/api/v1/simulate-storm", json={"properties": stripped})

    assert response.status_code == 422
    assert response.json()["detail"]["propertyIds"]


def test_unknown_vulnerability_class_is_an_actionable_422(request_properties):
    broken = [{**request_properties[0], "vulnerability_class": "adobe_hut"}]
    response = client.post("/api/v1/simulate-storm", json={"properties": broken})

    assert response.status_code == 422
    assert "adobe_hut" in response.json()["detail"]["message"]


def test_simulator_endpoint_describes_what_it_is_and_is_not():
    info = client.get("/api/v1/simulator").json()

    assert info["package"] == "hurricane_simulator"
    assert "bounding box is not used" in info["florida_strike_definition"]
    assert "not an expected loss" in info["not_a_rate"].lower() or "NOT a typical" in info["not_a_rate"]
