"""Storm generation: POST /api/v1/storms/generate, and pricing what it returns.

The simulator is loaded on the first generate request (about 890 MB for its land/sea
map), so these also check that the API does not load it at start-up.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest
from starlette.testclient import TestClient

from app import generator, wind
from app.main import app

client = TestClient(app)
BACKEND = pathlib.Path(__file__).resolve().parents[1]

# South-east Bahamas: open water, inside the historical genesis region, and close
# enough to Florida that some members reach the demo portfolio.
START = {
    "latitude": 22.5,
    "longitude": -72.0,
    "max_wind_kt": 60,
    "seed": 42,
    "count": 4,
    "start_date": "2026-09-10",
}


@pytest.fixture(scope="module")
def generated() -> dict:
    response = client.post("/api/v1/storms/generate", json=START)
    assert response.status_code == 200, response.text
    return response.json()


def _frontend_properties() -> list[dict]:
    portfolio = json.loads((BACKEND / "app" / "fixtures" / "example_portfolio.json").read_text(encoding="utf-8"))
    return [
        {"id": index + 1, "latitude": entry["latitude"], "longitude": entry["longitude"],
         "value": entry["replacement_cost_usd"]}
        for index, entry in enumerate(portfolio["properties"])
    ]


def test_the_api_starts_without_loading_the_simulator():
    code = (
        "import sys, app.main; "
        "print(sorted({'hurricane_simulator', 'global_land_mask', 'scipy'} & set(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_generates_the_requested_storms_from_the_chosen_start(generated):
    storms = generated["storms"]

    assert [storm["storm_id"] for storm in storms] == ["G42-01", "G42-02", "G42-03", "G42-04"]
    assert generated["storm_ids"] == [storm["storm_id"] for storm in storms]
    for storm in storms:
        first = storm["track"][0]
        assert (first["latitude"], first["longitude"], first["max_wind_kt"]) == (22.5, -72.0, 60.0)
        assert first["timestamp"] == "2026-09-10 00:00:00"
        assert [point["step"] for point in storm["track"]] == list(range(len(storm["track"])))
        assert wind.track_gaps(storm) == []


def test_the_same_request_generates_the_same_storms(generated):
    again = client.post("/api/v1/storms/generate", json=START).json()
    assert again["storms"] == generated["storms"]


def test_the_generated_catalog_says_what_it_is(generated):
    import hurricane_simulator

    assert "not a probabilistic sample" in generated["completeness_warning"]
    assert generated["generator"]["version"] == hurricane_simulator.__version__
    assert generated["generator"]["seed"] == 42
    assert len(generated["generator"]["member_seeds"]) == 4
    assert generated["wind_model"]["model"] == "wind_field"


def test_generated_storms_price_through_storm_losses(generated):
    response = client.post(
        "/api/v1/storm-losses",
        json={"properties": _frontend_properties(), "storms": generated["storms"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["storm_ids"] == generated["storm_ids"]
    priced = {(row["storm_id"], row["property_id"]) for row in body["rows"]}
    assert priced == {(storm_id, property_id) for storm_id in body["storm_ids"] for property_id in body["property_ids"]}
    assert body["metadata"]["storm_catalog"]["catalog_id"] == "supplied-with-request"
    assert not any("stored track jumps" in warning for warning in body["warnings"])


def test_one_generated_storm_prices_the_way_the_dashboard_sends_it(generated):
    storm = generated["storms"][0]
    response = client.post(
        "/api/v1/storm-losses",
        json={"properties": _frontend_properties(), "storms": [storm], "storm_ids": [storm["storm_id"]]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["storm_ids"] == [storm["storm_id"]]


def test_the_simulator_is_loaded_once_and_reused(generated):
    assert generator.is_loaded()
    assert generator.storm_generator() is generator.storm_generator()


def test_a_start_over_land_is_a_422():
    response = client.post("/api/v1/storms/generate", json={**START, "latitude": 28.54, "longitude": -81.38})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "GenerationInputError"
    assert "land" in response.json()["detail"]["message"]


def test_a_start_far_from_where_storms_form_is_a_422():
    response = client.post("/api/v1/storms/generate", json={**START, "latitude": -20.0, "longitude": -30.0})

    assert response.status_code == 422
    assert "historical" in response.json()["detail"]["message"]


def test_more_than_ten_storms_is_a_422():
    assert client.post("/api/v1/storms/generate", json={**START, "count": 11}).status_code == 422


def test_supplied_storms_need_their_own_ids(generated):
    storm = generated["storms"][0]
    response = client.post(
        "/api/v1/storm-losses", json={"properties": _frontend_properties(), "storms": [storm, storm]}
    )

    assert response.status_code == 422
    assert response.json()["detail"]["duplicateStormIds"] == [storm["storm_id"]]


def test_asking_for_a_storm_that_was_not_supplied_is_a_404(generated):
    response = client.post(
        "/api/v1/storm-losses",
        json={"properties": _frontend_properties(), "storms": generated["storms"][:1], "storm_ids": ["SYN0155"]},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["unknownStormIds"] == ["SYN0155"]


def test_the_default_start_date_is_in_the_hurricane_season():
    assert generator.default_start_date().month == 9
