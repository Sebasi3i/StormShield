"""Florida batches: POST /api/v1/storms/generate-florida, and pricing what it returns.

One batch is generated per module (the search takes a few seconds). season_year is
pinned so the batch does not change with the calendar.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import json
import pathlib

import pytest
from starlette.testclient import TestClient

from app import florida, generator, wind
from app.main import app

client = TestClient(app)
BACKEND = pathlib.Path(__file__).resolve().parents[1]

REQUEST = {"seed": 42, "season_year": 2026}


@pytest.fixture(scope="module")
def batch() -> dict:
    response = client.post("/api/v1/storms/generate-florida", json=REQUEST)
    assert response.status_code == 200, response.text
    return response.json()


def _frontend_properties() -> list[dict]:
    portfolio = json.loads((BACKEND / "app" / "fixtures" / "example_portfolio.json").read_text(encoding="utf-8"))
    return [
        {"id": index + 1, "latitude": entry["latitude"], "longitude": entry["longitude"],
         "value": entry["replacement_cost_usd"]}
        for index, entry in enumerate(portfolio["properties"])
    ]


def test_the_batch_has_ten_storms_from_one_start(batch):
    storms = batch["storms"]

    assert len(storms) == 10
    assert [storm["storm_id"] for storm in storms] == [f"FL42-{index:02d}" for index in range(1, 11)]
    assert batch["storm_ids"] == [storm["storm_id"] for storm in storms]
    start = batch["generator"]["start"]
    for storm in storms:
        first = storm["track"][0]
        assert (first["latitude"], first["longitude"]) == (start["latitude"], start["longitude"])
        assert first["max_wind_kt"] == start["max_wind_kt"] == 70.0
        assert first["timestamp"] == start["time"]
        assert [point["step"] for point in storm["track"]] == list(range(len(storm["track"])))
        assert wind.track_gaps(storm) == []


def test_at_least_two_storms_hit_florida_as_major_hurricanes(batch):
    hits = [storm for storm in batch["storms"] if storm["florida_hit"]]

    assert len(hits) >= 2
    assert batch["florida"]["hits"] == [storm["storm_id"] for storm in hits]
    assert batch["florida"]["min_wind_kt"] == 96.0
    for storm in hits:
        assert storm["florida_peak_wind_kt"] >= 96.0
        assert storm["florida_first_time"] is not None
        # The flag is recomputable from the track it travels with.
        assert florida.florida_points(storm["track"], 96.0)


def test_misses_are_flagged_as_misses(batch):
    misses = [storm for storm in batch["storms"] if not storm["florida_hit"]]

    for storm in misses:
        assert storm["florida_peak_wind_kt"] is None
        assert storm["florida_first_time"] is None
        assert florida.florida_points(storm["track"], 96.0) == []


def test_the_search_is_recorded(batch):
    assert 1 <= batch["florida"]["starts_tried"] <= batch["florida"]["max_starts"]
    assert batch["florida"]["min_hits_requested"] == 2
    assert batch["generator"]["seed"] == 42
    assert batch["generator"]["season_year"] == 2026
    assert len(batch["generator"]["member_seeds"]) == 10
    assert "SELECTED" in batch["completeness_warning"]
    assert "not a probabilistic sample" in batch["completeness_warning"].lower()
    assert batch["wind_model"]["model"] == "wind_field"


def test_the_same_request_returns_the_same_batch(batch):
    again = client.post("/api/v1/storms/generate-florida", json=REQUEST).json()
    assert again["storms"] == batch["storms"]
    assert again["catalog_id"] == batch["catalog_id"]


def test_the_whole_batch_prices_through_storm_losses_in_one_run(batch):
    response = client.post(
        "/api/v1/storm-losses",
        json={"properties": _frontend_properties(), "storms": batch["storms"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["storm_ids"] == batch["storm_ids"]
    priced = {(row["storm_id"], row["property_id"]) for row in body["rows"]}
    assert priced == {(storm_id, property_id) for storm_id in body["storm_ids"] for property_id in body["property_ids"]}
    assert body["metadata"]["storm_catalog"]["catalog_id"] == "supplied-with-request"


def test_min_hits_cannot_exceed_count():
    response = client.post("/api/v1/storms/generate-florida", json={"seed": 1, "count": 3, "min_florida_hits": 4})
    assert response.status_code == 422


def test_more_than_ten_storms_is_a_422():
    assert client.post("/api/v1/storms/generate-florida", json={"seed": 1, "count": 11}).status_code == 422


def test_a_search_that_finds_nothing_is_a_422(monkeypatch):
    monkeypatch.setattr(generator, "MAX_STARTS_PER_BATCH", 1)
    # One 20 kt start never reaches Florida as a major hurricane, so the search fails fast.
    response = client.post(
        "/api/v1/storms/generate-florida",
        json={"seed": 5, "max_wind_kt": 20, "min_florida_hits": 10, "season_year": 2026},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "GenerationInputError"
    assert "No starting point" in response.json()["detail"]["message"]
