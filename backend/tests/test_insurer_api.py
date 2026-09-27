"""The sample-insurer endpoints: the demo bundle, compare and optimize, and the 422s.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)
CATALOG = ["SYN0155", "SYN0697", "SYN0973"]
ANNUAL = {"kind": "one_event_or_none", "annual_event_probability": 0.1, "conditional_storm_weights": {s: 1 for s in CATALOG}}


def test_demo_bundle_has_everything_a_client_needs():
    demo = client.get("/api/v1/insurer/demo").json()
    assert demo["schema_version"] == "insurer-demo-v1"
    assert demo["insurer"]["insurer_id"] == "insurer-demo-001"
    assert demo["default_preset_id"] == "app_consistent_demo" and set(demo["presets"]) == {"workbook_reference", "app_consistent_demo"}
    assert demo["credit_plan"]["credit_plan"]["shutters_roof_straps"] == 0.25
    assert [p["policy_id"] for p in demo["policies"]] == [f"DEMO-P{n:03d}" for n in range(1, 11)]
    assert demo["frontend_property_map"]["1"] == "P001"
    assert demo["available_storm_ids"] == CATALOG
    assert demo["program_defaults"]["budget_usd"] == 20000
    assert demo["reference_totals"]["app_consistent_demo"]["insurer_upfront_usd"] == 16818.0


def test_compare_defaults_to_every_policy_proposal_and_storm_in_event_only_mode():
    out = client.post("/api/v1/insurer/compare", json={}).json()
    assert out["schema_version"] == "insurer-demo-v1" and out["preset_id"] == "app_consistent_demo"
    assert out["storm_ids"] == CATALOG and len(out["policies"]) == 10 and len(out["loss_rows"]) == 30
    assert out["complete"] is True and out["unavailable"] == []
    assert set(out["programs"]) == {"current_book", "homeowner_funded", "insurer_cofunded"}
    assert out["programs"]["insurer_cofunded"]["annual_economics"] is None
    assert out["programs"]["insurer_cofunded"]["costs"]["insurer_upfront_usd"] == 16818.0
    assert out["events"]["SYN0155"]["avoided_payout_usd"] > 700_000
    assert out["provenance"]["curve_set_id"] == "hazus-msf1-suburban-v3"


def test_compare_with_the_annual_model_returns_npv_and_break_even():
    out = client.post("/api/v1/insurer/compare", json={"storm_ids": CATALOG, "annual_model": ANNUAL}).json()
    econ = out["programs"]["insurer_cofunded"]["annual_economics"]
    assert econ["insurer_npv_usd"] > 0 and 0.05 < econ["break_even_annual_event_probability"] < 0.1
    assert econ["assumption"]["annual_event_probability"] == 0.1
    assert out["annual_model"]["kind"] == "one_event_or_none"


def test_compare_subset_and_deductible_sensitivity():
    out = client.post("/api/v1/insurer/compare", json={
        "policy_ids": ["DEMO-P001", "DEMO-P002"], "storm_ids": ["SYN0155"], "selected_proposal_ids": ["UPG-P001"], "deductible_fraction": 0.1,
    }).json()
    assert out["policy_ids"] == ["DEMO-P001", "DEMO-P002"] and out["selected_proposal_ids"] == ["UPG-P001"]
    assert out["programs"]["insurer_cofunded"]["project_count"] == 1
    assert "held fixed" in out["deductible_sensitivity_note"]
    assert all(r["deductible_usd"] == 0.1 * r["coverage_limit_usd"] for r in out["loss_rows"])


def test_optimize_returns_the_selection_and_the_comparison_on_it():
    out = client.post("/api/v1/insurer/optimize", json={"storm_ids": CATALOG, "annual_model": ANNUAL}).json()
    opt = out["optimization"]
    assert opt["objective"] == "insurer_npv" and opt["subsets_evaluated"] == 512
    assert opt["insurer_upfront_usd"] <= 20000 and opt["insurer_npv_usd"] >= 0
    assert out["selected_proposal_ids"] == opt["selected_proposal_ids"]


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"storm_ids": ["SYN9999"]}, "unknown storm id"),
        ({"policy_ids": ["DEMO-P099"]}, "unknown policy ids"),
        ({"preset_id": "workbook_reference"}, "physical-state conflicts"),
        ({"annual_model": {**ANNUAL, "conditional_storm_weights": {"SYN0155": 1}}}, "exactly the storms"),
        ({"selected_proposal_ids": ["UPG-nope"]}, "proposal ids"),
    ],
)
def test_bad_inputs_are_422_with_the_reason(body, fragment):
    response = client.post("/api/v1/insurer/compare", json={"storm_ids": CATALOG, **body})
    assert response.status_code == 422, response.text
    assert fragment in response.json()["detail"]["message"]


def test_schema_level_rejections_are_422_too():
    assert client.post("/api/v1/insurer/compare", json={"program": {"grant_share": 1.5}}).status_code == 422
    assert client.post("/api/v1/insurer/compare", json={"annual_model": {"kind": "poisson"}}).status_code == 422
    assert client.post("/api/v1/insurer/compare", json={"deductible_fraction": -0.1}).status_code == 422


def test_optimize_without_the_annual_model_is_422():
    response = client.post("/api/v1/insurer/optimize", json={"storm_ids": CATALOG})
    assert response.status_code == 422 and "annual model" in response.json()["detail"]["message"]

