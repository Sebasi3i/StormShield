"""The simulated storm climatology: vectorised pricing against claims.py, expected
yearly figures, the map's average year, and the insurer's simulated_climate model.

The arithmetic tests use a tiny inline climatology so the shipped 5,000-storm fixture
cannot quietly rewrite them; the fixture tests then hold the shipped file to its own
provenance and to the curve set's range.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import math

import pytest

from app import claims, climatology, insurer, mitigation_states as ms, premium

METRIC = "peak_3s_gust_10m_open_terrain_mph"


def curve(vclass, upgrade, features, at_100, roof="blended"):
    return claims.Curve(f"fx.{vclass}.{upgrade}.{roof}", vclass, upgrade, METRIC,
                        ((0.0, 0.0), (50.0, 0.0), (100.0, at_100), (200.0, 1.0)), "assumed", "synthetic", roof, features)


CURVES = {(c.vulnerability_class, c.upgrade_id, c.roof_shape): c for c in (
    curve("pre", "baseline", (), 0.375), curve("pre", "shutters", ("shutters",), 0.25),
    curve("pre", "roof_straps", ("roof_straps",), 0.30), curve("pre", "shutters_roof_straps", ("roof_straps", "shutters"), 0.20),
)}
CURVE_SET = {"curve_set_id": "fx", "wind_metric": METRIC, "evidence_status": "assumed", "provenance": {}, "curves": CURVES}

# Four storms: one big hit on A only, one moderate on both, two misses.
CLIM = {
    "climatology_id": "fx-4", "evidence_status": "simulated", "summary": "fixture",
    "storms_per_year": {"default": "recent", "recent": {"storms_per_year": 10.0}, "whole_record": {"storms_per_year": 8.0}},
    "gust_columns": ["A", "B"], "max_gust_mph": 150.0,
    "storms": [
        {"storm_id": "S1", "peak_wind_kt": 120, "gusts_mph": [150.0, 0.0]},
        {"storm_id": "S2", "peak_wind_kt": 80, "gusts_mph": [100.0, 100.0]},
        {"storm_id": "S3", "peak_wind_kt": 40, "gusts_mph": [0.0, 30.0]},
        {"storm_id": "S4", "peak_wind_kt": 40, "gusts_mph": [0.0, 0.0]},
    ],
}


def state(pid, value=400_000.0, current=(), resulting=("shutters",)):
    return ms.PropertyState(pid, value, "pre", "unknown", premium.canonical_features(current), premium.canonical_features(resulting))


def policy(pid, value=400_000.0):
    return claims.policy_from_template(pid, value, {"deductible": {"percent": 0.05, "basis": "percent_of_coverage_a"}})


# --------------------------------------------------------------------------- #
# Arithmetic on the inline fixture
# --------------------------------------------------------------------------- #


def test_expected_annual_is_rate_times_mean_per_storm_and_matches_claims_per_storm():
    out = climatology.expected_annual([state("A"), state("B")], [policy("A"), policy("B")], CLIM, curve_set=CURVE_SET)
    # A at 150 mph: fraction 0.6875 -> damage 275k, payout 255k; at 100 mph: 150k / 130k. B at 100: 130k; at 30: 0.
    per_storm_current = [255_000 + 0, 130_000 + 130_000, 0, 0]
    per_storm_result = [(0.625 * 400_000 - 20_000) + 0, 80_000 + 80_000, 0, 0]  # shutters: 0.25@100, 0.625@150
    assert out["mean_per_storm"]["current_payout_usd"] == pytest.approx(sum(per_storm_current) / 4)
    assert out["expected_annual"]["current_payout_usd"] == pytest.approx(10 * sum(per_storm_current) / 4)
    assert out["expected_annual"]["after_payout_usd"] == pytest.approx(10 * sum(per_storm_result) / 4)
    assert out["expected_annual"]["avoided_payout_usd"] == pytest.approx(10 * (sum(per_storm_current) - sum(per_storm_result)) / 4)
    # the same storm through claims.compute_losses agrees to the cent
    reference = claims.compute_losses(
        [claims.Property("A", 400_000.0, "pre")], [policy("A")], [claims.WindExposure("S1", "A", 150.0, METRIC)], ["S1"],
        run_id="t", catalog_id="t", sampling_description="t", curve_set=CURVE_SET,
    )["rows"]
    shutters = next(r for r in reference if r["upgrade_id"] == "shutters")
    assert shutters["baseline_payout_usd"] == 255_000.0 and shutters["upgraded_payout_usd"] == 230_000.0


def test_shares_and_poisson_probability_of_a_paying_year():
    out = climatology.expected_annual([state("A"), state("B")], [policy("A"), policy("B")], CLIM, curve_set=CURVE_SET)
    assert out["share_of_storms"]["with_any_payout_current"] == 0.5
    assert out["share_of_storms"]["with_any_repair_cost"] == 0.5  # S3's 30 mph is below the curve's first point
    assert out["probability_of_a_year_with_any_payout"]["current"] == pytest.approx(1 - math.exp(-10 * 0.5), abs=1e-4)
    assert out["per_property"]["B"]["share_of_storms_with_payout"] == 0.25
    assert out["per_property"]["A"]["expected_annual_avoided_payout_usd"] == pytest.approx(10 * ((255_000 - 230_000) + (130_000 - 80_000)) / 4)


def test_storms_per_year_override_and_validation():
    out = climatology.expected_annual([state("A")], [policy("A")], CLIM, storms_per_year_override=2.0, curve_set=CURVE_SET)
    assert out["storms_per_year"] == 2.0
    assert out["expected_annual"]["current_payout_usd"] == pytest.approx(2 * (255_000 + 130_000) / 4)
    with pytest.raises(climatology.ClimatologyError, match="positive"):
        climatology.storms_per_year(CLIM, 0)
    with pytest.raises(climatology.ClimatologyError, match="no gusts for"):
        climatology.gust_matrix(CLIM, ["A", "Z"])
    with pytest.raises(climatology.ClimatologyError, match="supported range"):
        climatology.validate_climatology({**CLIM, "max_gust_mph": 250.0}, CURVE_SET)


def test_return_periods_pick_the_kth_largest_storm():
    out = climatology.expected_annual([state("A"), state("B")], [policy("A"), policy("B")], CLIM, curve_set=CURVE_SET)
    # rate 10/yr, 4 storms: once per 10 years -> share 0.01 -> k = 0 -> null; too small a sample
    assert out["return_periods_current_payout"]["once_per_10_years_usd"] is None
    big = {**CLIM, "storms": [dict(s) for s in CLIM["storms"]] * 100}  # 400 storms
    out = climatology.expected_annual([state("A"), state("B")], [policy("A"), policy("B")], big, curve_set=CURVE_SET)
    # share 0.01 of 400 = 4 storms: the 4th largest current payout is S1's 255k (S2's 260k is larger 100x, S1 100x)
    assert out["return_periods_current_payout"]["once_per_10_years_usd"] == 260_000.0


def test_average_year_for_properties_reports_each_upgrade():
    props = [claims.Property("1", 400_000.0, "pre")]
    out = climatology.average_year_for_properties(props, [policy("1")], {"1": "A"}, CLIM, curve_set=CURVE_SET)
    row = out["properties"][0]
    assert row["climatology_property_id"] == "A" and row["installed_features"] == []
    assert row["expected_annual_repair_cost_usd"] == pytest.approx(10 * (275_000 + 150_000) / 4)
    assert row["expected_annual_payout_usd"] == pytest.approx(10 * (255_000 + 130_000) / 4)
    assert set(row["upgrades"]) == {"shutters", "roof_straps", "shutters_roof_straps"}
    assert row["upgrades"]["shutters_roof_straps"]["features_added"] == ["roof_straps", "shutters"]
    assert row["upgrades"]["shutters"]["expected_annual_avoided_payout_usd"] == pytest.approx(10 * (25_000 + 50_000) / 4)
    assert row["probability_of_damage_in_a_year"] == pytest.approx(1 - math.exp(-10 * 0.5), abs=1e-4)


# --------------------------------------------------------------------------- #
# The shipped fixture and the insurer integration
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def shipped():
    climatology.reset_caches()
    claims.reset_caches()
    premium.reset_caches()
    return climatology.load_climatology()


def test_shipped_climatology_is_a_large_unselected_sample_with_provenance(shipped):
    assert shipped["evidence_status"] == "simulated" and len(shipped["storms"]) >= 5000
    assert shipped["gust_columns"] == [f"P{n:03d}" for n in range(1, 11)]
    assert shipped["storms_per_year"]["default"] == "recent"
    assert shipped["storms_per_year"]["recent"]["storms_per_year"] > shipped["storms_per_year"]["whole_record"]["storms_per_year"]
    assert shipped["simulator"]["method"] == "simulate_hurricanes" and shipped["simulator"]["seed"] == 2026
    assert shipped["pruning"]["storms_through_wind_field"] + shipped["pruning"]["storms_pruned"] == len(shipped["storms"])
    # most random Atlantic storms never touch the book: that is the point of the sample
    touched = sum(1 for s in shipped["storms"] if any(g > 0 for g in s["gusts_mph"]))
    assert 0.05 < touched / len(shipped["storms"]) < 0.5
    assert shipped["max_gust_mph"] <= 250
    import hashlib, pathlib
    portfolio = pathlib.Path(__file__).resolve().parents[1] / "app" / "fixtures" / "example_portfolio.json"
    assert shipped["portfolio"]["example_portfolio_sha256"] == hashlib.sha256(portfolio.read_bytes()).hexdigest()


def test_insurer_compare_under_the_simulated_climate(shipped):
    out = insurer.compare(storm_ids=["SYN0155"], annual_model={"kind": "simulated_climate"})
    assert out["annual_model"]["kind"] == "simulated_climate" and out["annual_model"]["sample_storms"] == len(shipped["storms"])
    climate = out["climate"]
    assert climate["storms_per_year"] == shipped["storms_per_year"]["recent"]["storms_per_year"]
    assert set(climate["per_policy"]) == {f"DEMO-P{n:03d}" for n in range(1, 11)}
    econ = out["programs"]["insurer_cofunded"]["annual_economics"]
    assert econ["assumption"]["kind"] == "simulated_climate"
    assert econ["break_even_annual_event_probability"] is None and econ["break_even_storms_per_year"] is not None
    assert econ["expected_annual_avoided_payout_usd"] == climate["expected_annual"]["avoided_payout_usd"]
    # a fair sample makes the program far less attractive than the invented 10%
    invented = insurer.compare(storm_ids=["SYN0155", "SYN0697", "SYN0973"], annual_model={
        "kind": "one_event_or_none", "annual_event_probability": 0.1, "conditional_storm_weights": {"SYN0155": 1, "SYN0697": 1, "SYN0973": 1}})
    assert econ["insurer_npv_usd"] < invented["programs"]["insurer_cofunded"]["annual_economics"]["insurer_npv_usd"]
    # the events table is untouched by the annual model
    assert out["events"]["SYN0155"]["avoided_payout_usd"] > 700_000


def test_insurer_optimize_under_the_simulated_climate(shipped):
    out = insurer.optimize(storm_ids=["SYN0155"], annual_model={"kind": "simulated_climate", "storms_per_year": 14.0})
    opt = out["optimization"]
    assert opt["subsets_evaluated"] == 512 and out["annual_model"]["storms_per_year"] == 14.0
    assert opt["insurer_npv_usd"] >= 0.0
    assert set(opt["selected_proposal_ids"]) | set(opt["rejected_proposal_ids"]) == {c["proposal_id"] for c in opt["candidates"]}


def test_simulated_climate_rejects_a_mismatched_id_and_bad_rate(shipped):
    with pytest.raises(insurer.InsurerError, match="not the one on disk"):
        insurer.compare(storm_ids=["SYN0155"], annual_model={"kind": "simulated_climate", "climatology_id": "nope"})
    with pytest.raises(insurer.InsurerError, match="positive"):
        insurer.compare(storm_ids=["SYN0155"], annual_model={"kind": "simulated_climate", "storms_per_year": -1})
