"""The economics service: annual arithmetic, the optimizer, and the three program arms
on the real fixtures and catalog storms.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import pytest

from app import claims, insurer, premium

PROGRAM = {"grant_share": 0.25, "grant_cap_usd": 2500, "inspection_usd_per_project": 100, "fixed_setup_usd": 500,
           "annual_admin_usd": 0, "budget_usd": 20000, "horizon_years": 10, "discount_rate": 0.05}
CATALOG = ["SYN0155", "SYN0697", "SYN0973"]
ANNUAL = {"kind": "one_event_or_none", "annual_event_probability": 0.1, "conditional_storm_weights": {s: 1 for s in CATALOG}}


@pytest.fixture(autouse=True)
def fresh():
    premium.reset_caches()
    claims.reset_caches()


# --------------------------------------------------------------------------- #
# Annual arithmetic and model validation
# --------------------------------------------------------------------------- #


def test_annual_arithmetic_fixture_from_the_specification():
    econ = insurer.annual_economics(expected_annual_avoided_payout_usd=3000, annual_premium_foregone_usd=1000, annual_admin_usd=100,
                                    insurer_upfront_usd=500, horizon_years=2, discount_rate=0.0)
    assert econ["pv_factor"] == 2.0
    assert econ["insurer_npv_usd"] == 3300.0
    assert econ["break_even_annual_avoided_payout_usd"] == 1350.0
    assert econ["insurer_roi"] == round(3300 / 500, 4)


def test_roi_is_null_when_the_insurer_spends_nothing_and_negatives_stay_negative():
    econ = insurer.annual_economics(expected_annual_avoided_payout_usd=100, annual_premium_foregone_usd=1000, annual_admin_usd=0,
                                    insurer_upfront_usd=0, horizon_years=10, discount_rate=0.05)
    assert econ["insurer_roi"] is None and econ["insurer_npv_usd"] < 0


def test_annual_model_validation():
    with pytest.raises(insurer.InsurerError, match="exactly the storms"):
        insurer.validate_annual_model({**ANNUAL, "conditional_storm_weights": {"SYN0155": 1}}, CATALOG)
    with pytest.raises(insurer.InsurerError, match="nonnegative"):
        insurer.validate_annual_model({**ANNUAL, "conditional_storm_weights": {**ANNUAL["conditional_storm_weights"], "SYN0155": -1}}, CATALOG)
    with pytest.raises(insurer.InsurerError, match="positive"):
        insurer.validate_annual_model({**ANNUAL, "conditional_storm_weights": {s: 0 for s in CATALOG}}, CATALOG)
    with pytest.raises(insurer.InsurerError, match="\\[0, 1\\]"):
        insurer.validate_annual_model({**ANNUAL, "annual_event_probability": 1.5}, CATALOG)
    with pytest.raises(insurer.InsurerError, match="kind"):
        insurer.validate_annual_model({"kind": "poisson"}, CATALOG)
    ok = insurer.validate_annual_model(ANNUAL, CATALOG)
    assert ok["storm_probabilities"] == {s: pytest.approx(0.1 / 3) for s in CATALOG} and ok["no_event_probability"] == pytest.approx(0.9)
    assert insurer.validate_annual_model({**ANNUAL, "annual_event_probability": 0}, CATALOG)["storm_probabilities"] == {s: 0.0 for s in CATALOG}


# --------------------------------------------------------------------------- #
# The optimizer on synthetic candidates
# --------------------------------------------------------------------------- #


def cand(pid, cost, foregone, avoided, share=0.25, cap=2500):
    return {"proposal_id": pid, "effective_cost_usd": cost, "insurer_grant_usd": min(share * cost, cap, cost),
            "annual_premium_foregone_usd": foregone, "expected_annual_avoided_payout_usd": avoided}


def test_optimizer_picks_the_best_subset_and_charges_setup_once():
    good = cand("A", 8000, 1000, 5000)     # net +4000/yr, grant 2000
    bad = cand("B", 8000, 3000, 500)       # net -2500/yr
    also_good = cand("C", 4000, 500, 2000)  # net +1500/yr, grant 1000
    out = insurer.optimize_subsets([bad, good, also_good], PROGRAM)
    assert out["selected_proposal_ids"] == ["A", "C"]
    assert out["insurer_upfront_usd"] == 2000 + 1000 + 200 + 500  # setup once, inspections per project
    assert out["subsets_evaluated"] == 8
    pv = premium.pv_factor(10, 0.05)
    assert out["insurer_npv_usd"] == pytest.approx(-3700 + (7000 - 1500) * pv, abs=0.01)


def test_optimizer_respects_the_budget_and_evaluates_the_empty_subset():
    a = cand("A", 8000, 1000, 5000)
    b = cand("B", 8000, 1000, 5000)
    tight = {**PROGRAM, "budget_usd": 2700}  # one project: 2000 grant + 100 + 500 = 2600; two: 5100
    out = insurer.optimize_subsets([b, a], tight)
    assert out["selected_proposal_ids"] == ["A"]  # tie between A and B broken lexicographically
    assert out["budget_remaining_usd"] == 100.0
    none = insurer.optimize_subsets([cand("A", 8000, 5000, 100)], PROGRAM)
    assert none["selected_proposal_ids"] == [] and none["insurer_npv_usd"] == 0.0
    assert "No funded projects" in none["verdict"]


def test_optimizer_prefers_lower_spending_on_equal_npv():
    a = cand("A", 8000, 1000, 5000)
    zero = cand("Z", 400, 0, 0)  # adds nothing: grant 100 + inspection 100 for no benefit -> lower NPV, never chosen
    out = insurer.optimize_subsets([a, zero], PROGRAM)
    assert out["selected_proposal_ids"] == ["A"]


# --------------------------------------------------------------------------- #
# The comparison on the real fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def event_only():
    premium.reset_caches(); claims.reset_caches()
    return insurer.compare(storm_ids=CATALOG, program=PROGRAM)


@pytest.fixture(scope="module")
def annual():
    premium.reset_caches(); claims.reset_caches()
    return insurer.compare(storm_ids=CATALOG, program=PROGRAM, annual_model=ANNUAL)


def test_event_only_mode_returns_no_annual_estimate(event_only):
    for arm in insurer.ARMS:
        assert event_only["programs"][arm]["annual_economics"] is None
        assert "event_only" in event_only["programs"][arm]["annual_economics_unavailable_reason"]
    assert event_only["complete"] and event_only["preset_id"] == "app_consistent_demo"
    assert len(event_only["loss_rows"]) == 30 and len(event_only["policies"]) == 10


def test_baseline_payout_counts_each_policy_and_event_once(event_only):
    for storm_id in CATALOG:
        rows = [r for r in event_only["loss_rows"] if r["storm_id"] == storm_id]
        assert [r["policy_id"] for r in rows] == [f"DEMO-P{n:03d}" for n in range(1, 11)]
        assert event_only["events"][storm_id]["current_book"]["payout_usd"] == pytest.approx(sum(r["current_payout_usd"] for r in rows), abs=0.05)


def test_homeowner_funded_and_cofunded_arms_differ_only_in_cash(event_only):
    h, c = event_only["programs"]["homeowner_funded"], event_only["programs"]["insurer_cofunded"]
    assert h["premium"] == c["premium"] and h["project_count"] == c["project_count"] == 9
    assert h["costs"]["project_cost_usd"] == c["costs"]["project_cost_usd"] == 67_223.0
    assert h["costs"]["insurer_grants_usd"] == 0.0 and h["costs"]["insurer_upfront_usd"] == 900.0 + 500.0
    assert c["costs"]["insurer_grants_usd"] == 15_418.0 and c["costs"]["insurer_upfront_usd"] == 16_818.0
    assert h["costs"]["homeowner_upfront_usd"] + h["costs"]["insurer_grants_usd"] == pytest.approx(67_223.0)
    assert c["costs"]["homeowner_upfront_usd"] + c["costs"]["insurer_grants_usd"] == pytest.approx(67_223.0)
    for a, b in zip(event_only["policies"], event_only["policies_homeowner_funded"]):
        assert a["resulting_features"] == b["resulting_features"] and a["result_wind_premium_usd"] == b["result_wind_premium_usd"]


def test_current_book_has_no_costs_credits_or_benefits(event_only):
    cur = event_only["programs"]["current_book"]
    assert cur["project_count"] == 0 and cur["costs"]["insurer_upfront_usd"] == 0.0 and cur["costs"]["fixed_setup_usd"] == 0.0
    assert cur["premium"]["annual_premium_foregone_usd"] == 0.0
    assert cur["premium"]["current_wind_premium_usd"] == 104_436.4


def test_first_year_view_subtracts_one_years_concession_and_the_whole_upfront(event_only):
    e = event_only["events"]["SYN0155"]
    c = event_only["programs"]["insurer_cofunded"]
    assert e["first_year_insurer_benefit_if_this_storm_usd"]["insurer_cofunded"] == pytest.approx(
        e["avoided_payout_usd"] - 16_959.55 - 16_818.0, abs=0.02)
    assert e["first_year_insurer_benefit_if_this_storm_usd"]["current_book"] == 0.0
    assert e["avoided_payout_usd"] > 700_000 and e["current_book"]["payout_usd"] > 1_000_000
    zero = event_only["events"]["SYN0973"]
    assert zero["current_book"]["payout_usd"] == 0.0
    assert zero["first_year_insurer_benefit_if_this_storm_usd"]["insurer_cofunded"] == pytest.approx(-(16_959.55 + 16_818.0), abs=0.02)


def test_annual_model_gives_npv_break_even_and_the_break_even_probability(annual):
    c = annual["programs"]["insurer_cofunded"]["annual_economics"]
    events = annual["events"]
    mean_avoided = sum(events[s]["avoided_payout_usd"] for s in CATALOG) / 3
    assert c["expected_annual_avoided_payout_usd"] == pytest.approx(0.1 * mean_avoided, abs=0.05)
    pv = premium.pv_factor(10, 0.05)
    assert c["insurer_npv_usd"] == pytest.approx(-16_818 + (0.1 * mean_avoided - 16_959.55) * pv, abs=0.05)
    assert c["break_even_annual_avoided_payout_usd"] == pytest.approx(16_959.55 + 16_818 / pv, abs=0.02)
    assert c["break_even_annual_event_probability"] == pytest.approx((16_959.55 + 16_818 / pv) / mean_avoided, abs=1e-3)
    assert 0.05 < c["break_even_annual_event_probability"] < 0.10  # the preset lands just above break-even
    assert c["assumption"]["evidence_status"] == "invented_demo_assumption"
    h = annual["programs"]["homeowner_funded"]["annual_economics"]
    assert h["insurer_upfront_usd"] == 1_400.0 and h["insurer_npv_usd"] > c["insurer_npv_usd"]
    assert annual["programs"]["current_book"]["annual_economics"]["insurer_npv_usd"] == 0.0


def test_zero_event_probability_avoids_nothing():
    out = insurer.compare(storm_ids=CATALOG, program=PROGRAM, annual_model={**ANNUAL, "annual_event_probability": 0})
    econ = out["programs"]["insurer_cofunded"]["annual_economics"]
    assert econ["expected_annual_avoided_payout_usd"] == 0.0 and econ["break_even_annual_event_probability"] is None
    assert econ["insurer_npv_usd"] == pytest.approx(-16_818 - 16_959.55 * premium.pv_factor(10, 0.05), abs=0.05)


def test_deductible_change_preserves_damage_and_moves_loss_between_parties(event_only):
    ten = insurer.compare(storm_ids=["SYN0155"], program=PROGRAM, deductible_fraction=0.10)
    five = event_only["events"]["SYN0155"]
    assert ten["events"]["SYN0155"]["current_book"]["damage_usd"] == five["current_book"]["damage_usd"]
    assert ten["events"]["SYN0155"]["current_book"]["payout_usd"] < five["current_book"]["payout_usd"]
    assert ten["events"]["SYN0155"]["current_book"]["uninsured_damage_usd"] > five["current_book"]["uninsured_damage_usd"]
    assert ten["programs"]["insurer_cofunded"]["premium"] == event_only["programs"]["insurer_cofunded"]["premium"]
    assert "held fixed" in ten["deductible_sensitivity_note"]


def test_selecting_a_subset_leaves_the_others_unchanged(event_only):
    out = insurer.compare(storm_ids=["SYN0155"], program=PROGRAM, selected_proposal_ids=["UPG-P001"])
    by_id = {r["policy_id"]: r for r in out["loss_rows"]}
    assert not by_id["DEMO-P001"]["no_op"] and by_id["DEMO-P002"]["no_op"]
    assert out["programs"]["insurer_cofunded"]["project_count"] == 1
    assert out["programs"]["insurer_cofunded"]["costs"]["insurer_upfront_usd"] == 2058.5 + 100 + 500


def test_repeated_runs_are_identical(event_only):
    again = insurer.compare(storm_ids=CATALOG, program=PROGRAM)
    assert again["loss_rows"] == event_only["loss_rows"] and again["programs"] == event_only["programs"]
    assert again["provenance"]["input_config_sha256"] == event_only["provenance"]["input_config_sha256"]


def test_workbook_reference_preset_is_refused_for_physical_comparison():
    with pytest.raises(insurer.InsurerError, match="physical-state conflicts"):
        insurer.compare(preset_id="workbook_reference", storm_ids=["SYN0155"], program=PROGRAM)


def test_unknown_ids_are_rejected():
    with pytest.raises(insurer.InsurerError, match="unknown storm id"):
        insurer.compare(storm_ids=["SYN9999"], program=PROGRAM)
    with pytest.raises(insurer.InsurerError, match="unknown policy ids"):
        insurer.compare(policy_ids=["DEMO-P099"], storm_ids=["SYN0155"], program=PROGRAM)
    with pytest.raises(insurer.InsurerError, match="proposal ids"):
        insurer.compare(policy_ids=["DEMO-P001"], storm_ids=["SYN0155"], program=PROGRAM, selected_proposal_ids=["UPG-P002"])


def test_optimize_requires_the_annual_model_and_returns_a_selection():
    with pytest.raises(insurer.InsurerError, match="annual model"):
        insurer.optimize(storm_ids=CATALOG, program=PROGRAM)
    out = insurer.optimize(storm_ids=CATALOG, program=PROGRAM, annual_model=ANNUAL)
    opt = out["optimization"]
    assert opt["subsets_evaluated"] == 2 ** 9 and opt["excluded"] == []
    assert opt["insurer_upfront_usd"] <= 20_000
    assert set(opt["selected_proposal_ids"]) | set(opt["rejected_proposal_ids"]) == {c["proposal_id"] for c in opt["candidates"]}
    assert out["selected_proposal_ids"] == opt["selected_proposal_ids"]
    # the chosen subset's NPV, recomputed by the full comparison, matches the optimizer's
    econ = out["programs"]["insurer_cofunded"]["annual_economics"]
    if opt["selected_proposal_ids"]:
        assert econ["insurer_npv_usd"] == pytest.approx(opt["insurer_npv_usd"], abs=0.05)
    assert opt["insurer_npv_usd"] >= 0.0  # the empty subset is always available
