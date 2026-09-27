"""The premium engine's rules, and the workbook reconciliation the fixtures must reproduce.

Two kinds of test. The rule tests use tiny inline records, so a change to the shipped
fixtures cannot quietly rewrite the arithmetic they pin down. The reconciliation tests
read the shipped fixtures and hold them to the workbook's own totals and to the
specification's normalized totals, to the cent, which is the contract
scripts/import_insurer_workbook.py enforces when it writes them.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import premium

FIXTURES = Path(__file__).resolve().parents[1] / "app" / "fixtures"
EXTRACT = Path(__file__).resolve().parents[2] / "data" / "insurer_demo" / "workbook_extract.json"

# --------------------------------------------------------------------------- #
# Inline fixtures for the rule tests
# --------------------------------------------------------------------------- #

PLAN = {
    "plan_id": "test-plan",
    "credit_plan": {"none": 0.0, "shutters": 0.08, "roof_straps": 0.12, "shutters_roof_straps": 0.25},
    "zone_rates": {"HVHZ": 0.02, "other_florida": 0.013},
}

PROGRAM = {
    "grant_share": 0.25,
    "grant_cap_usd": 2500,
    "inspection_usd_per_project": 100,
    "fixed_setup_usd": 500,
    "annual_admin_usd": 0,
    "budget_usd": 20000,
    "horizon_years": 10,
    "discount_rate": 0.05,
}


def policy(
    policy_id: str = "T-1",
    *,
    installed=(),
    credited=None,
    requested=("shutters",),
    quoted_scope=None,
    quote=8234.0,
    confirmed=False,
    rating_value=850_000.0,
    zone="HVHZ",
    nonwind=None,
    proposal=True,
) -> dict:
    credited = installed if credited is None else credited
    record = {
        "policy_id": policy_id,
        "insurer_id": "test",
        "property_id": policy_id.replace("T-", "P"),
        "coverage_a_usd": rating_value,
        "coverage_limit_usd": rating_value,
        "deductible": {"kind": "percent", "fraction": 0.05},
        "rating_value_usd": rating_value,
        "rating_zone": zone,
        "annual_nonwind_premium_usd": nonwind,
        "states": {"demo": {"installed_features": list(installed), "credited_features": list(credited)}},
    }
    if proposal:
        record["proposal"] = {
            "proposal_id": f"UPG-{policy_id}",
            "requested_features": list(requested),
            "quoted_scope": list(requested if quoted_scope is None else quoted_scope),
            "quote_usd": quote,
            "incremental_scope_confirmed": confirmed,
        }
    return record


def price(record: dict, **kwargs) -> dict:
    return premium.price_policy(record, "demo", PLAN, PROGRAM, **kwargs)


# --------------------------------------------------------------------------- #
# Features and credits
# --------------------------------------------------------------------------- #


def test_features_are_canonical_sorted_and_deduplicated():
    assert premium.canonical_features(["shutters", "roof_straps", "shutters"]) == ("roof_straps", "shutters")
    assert premium.canonical_features(None) == ()


def test_unknown_feature_is_rejected():
    with pytest.raises(premium.PremiumError, match="unknown mitigation feature"):
        premium.canonical_features(["impact_glass"])


def test_combined_credit_is_the_lookup_tier_not_the_sum():
    assert premium.credit_for(["roof_straps", "shutters"], PLAN) == 0.25
    assert premium.credit_for(["shutters", "roof_straps"], PLAN) == 0.25  # order-independent
    assert premium.credit_for([], PLAN) == 0.0
    assert premium.credit_for(["shutters"], PLAN) + premium.credit_for(["roof_straps"], PLAN) == 0.20


def test_credit_outside_unit_interval_is_rejected():
    bad = {"plan_id": "bad", "credit_plan": {**PLAN["credit_plan"], "shutters": 1.5}, "zone_rates": PLAN["zone_rates"]}
    with pytest.raises(premium.PremiumError, match="at most 1"):
        premium.credit_for(["shutters"], bad)


def test_wind_premium_is_rating_value_times_rate_times_one_minus_credit():
    assert premium.wind_premium(850_000, 0.02, 0.08) == pytest.approx(15_640.0)
    assert premium.wind_premium(1_200_000, 0.02, 0.12) == pytest.approx(21_120.0)


# --------------------------------------------------------------------------- #
# Transitions and quotes
# --------------------------------------------------------------------------- #


def test_transition_adds_only_what_is_missing():
    added, resulting = premium.resolve_transition(["roof_straps"], ["shutters"])
    assert added == ("shutters",)
    assert resulting == ("roof_straps", "shutters")


def test_repeated_feature_is_no_project_and_costs_nothing():
    added, resulting = premium.resolve_transition(["roof_straps"], ["roof_straps"])
    assert added == () and resulting == ("roof_straps",)
    assert premium.effective_cost(1334.0, ["roof_straps"], added, False) == (0.0, None)


def test_quote_matching_the_added_features_is_used():
    assert premium.effective_cost(8234.0, ["shutters"], ("shutters",), False) == (8234.0, None)


def test_package_quote_overlapping_installed_features_needs_confirmation():
    cost, reason = premium.effective_cost(7542.0, ["roof_straps", "shutters"], ("shutters",), False)
    assert cost is None and reason == premium.QUOTE_SCOPE_UNCONFIRMED
    assert premium.effective_cost(7542.0, ["roof_straps", "shutters"], ("shutters",), True) == (7542.0, None)


def test_missing_or_incomplete_quote_is_null_never_zero():
    assert premium.effective_cost(None, ["shutters"], ("shutters",), False) == (None, premium.QUOTE_MISSING)
    assert premium.effective_cost(5000.0, ["shutters"], ("roof_straps", "shutters"), True) == (None, premium.QUOTE_SCOPE_INCOMPLETE)


def test_grant_is_share_capped_and_never_exceeds_cost():
    assert premium.grant_for(8234.0, 0.25, 2500) == pytest.approx(2058.5)
    assert premium.grant_for(15_551.0, 0.25, 2500) == 2500.0
    assert premium.grant_for(100.0, 1.0, 2500) == 100.0
    assert premium.grant_for(0.0, 0.25, 2500) == 0.0


# --------------------------------------------------------------------------- #
# Time value and homeowner view
# --------------------------------------------------------------------------- #


def test_pv_factor_is_horizon_at_zero_rate():
    assert premium.pv_factor(2, 0.0) == 2.0
    assert premium.pv_factor(10, 0.05) == pytest.approx(7.7217, abs=1e-4)


def test_pv_factor_rejects_bad_horizon_or_rate():
    with pytest.raises(premium.PremiumError):
        premium.pv_factor(0, 0.05)
    with pytest.raises(premium.PremiumError):
        premium.pv_factor(10, 1.5)


def test_payback_rules():
    assert premium.premium_only_payback_years(6175.5, 1360.0, True) == (pytest.approx(4.5408, abs=1e-3), None)
    assert premium.premium_only_payback_years(0.0, 500.0, True) == (0.0, None)
    assert premium.premium_only_payback_years(None, 500.0, True)[0] is None
    assert premium.premium_only_payback_years(1000.0, 0.0, True)[0] is None
    assert premium.premium_only_payback_years(0.0, 0.0, False) == (None, "No new project.")


def test_money_rounds_half_up_to_cents():
    assert premium.money(9100.505) == 9100.51
    assert premium.money(0.004) == 0.0
    assert premium.money(-0.004) == 0.0
    assert premium.money(None) is None


# --------------------------------------------------------------------------- #
# One policy
# --------------------------------------------------------------------------- #


def test_priced_policy_matches_the_workbook_row_for_p001():
    row = price(policy())
    assert row["is_project"] and row["features_added"] == ["shutters"]
    assert row["current_wind_premium_usd"] == 17_000.0
    assert row["result_wind_premium_usd"] == 15_640.0
    assert row["annual_premium_foregone_usd"] == 1_360.0
    assert row["effective_cost_usd"] == 8_234.0
    assert row["insurer_grant_usd"] == 2_058.5
    assert row["homeowner_upfront_usd"] == 6_175.5
    assert row["homeowner"]["premium_only_payback_years"] == pytest.approx(4.54, abs=0.01)
    assert row["homeowner"]["ten_year_undiscounted_premium_net_usd"] == pytest.approx(13_600 - 6_175.5)
    assert row["homeowner"]["premium_only_npv_usd"] == pytest.approx(-6_175.5 + 1_360 * premium.pv_factor(10, 0.05), abs=0.01)


def test_incremental_savings_compare_current_credit_to_resulting_credit():
    row = price(policy(installed=["roof_straps"], requested=["shutters"], rating_value=1_200_000, quote=5593.0))
    assert row["current_credit"] == 0.12 and row["result_credit"] == 0.25
    assert row["annual_premium_foregone_usd"] == 3_120.0


def test_no_op_proposal_is_not_a_project():
    row = price(policy(installed=["roof_straps"], requested=["roof_straps"], quote=1334.0, zone="other_florida", rating_value=460_000))
    assert not row["is_project"]
    assert row["effective_cost_usd"] == 0.0 and row["insurer_grant_usd"] == 0.0 and row["homeowner_upfront_usd"] == 0.0
    assert row["annual_premium_foregone_usd"] == 0.0
    assert row["homeowner"]["payback_note"] == "No new project."


def test_unselected_proposal_leaves_the_policy_unchanged():
    row = price(policy(), selected=False)
    assert not row["selected"] and not row["is_project"]
    assert row["result_wind_premium_usd"] == row["current_wind_premium_usd"]
    assert row["effective_cost_usd"] == 0.0


def test_credit_only_difference_changes_premium_only():
    # Straps installed but not credited: the premium is the uncredited one until a
    # credit-only correction says otherwise, and a shutters project then credits
    # shutters alone (8%), not the pair.
    row = price(policy(installed=["roof_straps"], credited=[], requested=["shutters"]))
    assert row["current_credit"] == 0.0 and row["result_credit"] == 0.08
    assert row["resulting_features"] == ["roof_straps", "shutters"]
    assert row["resulting_credited_features"] == ["shutters"]


def test_unknown_nonwind_premium_keeps_total_premium_null_but_wind_savings_known():
    row = price(policy(nonwind=None))
    assert row["current_total_premium_usd"] is None and row["result_total_premium_usd"] is None
    assert row["annual_premium_foregone_usd"] == 1_360.0
    row = price(policy(nonwind=2_000.0))
    assert row["current_total_premium_usd"] == 19_000.0 and row["result_total_premium_usd"] == 17_640.0


def test_unconfirmed_package_quote_yields_null_cost_with_reason_and_valid_premiums():
    row = price(policy(installed=["roof_straps"], requested=["roof_straps", "shutters"], quote=7542.0))
    assert row["is_project"] and row["features_added"] == ["shutters"]
    assert row["effective_cost_usd"] is None and row["cost_unavailable_reason"] == premium.QUOTE_SCOPE_UNCONFIRMED
    assert row["insurer_grant_usd"] is None and row["homeowner_upfront_usd"] is None
    assert row["annual_premium_foregone_usd"] == pytest.approx(17_000 * (0.12 - 0.25) * -1)
    assert row["homeowner"]["payback_note"] == "Project cost unknown."


def test_credited_features_must_be_installed():
    with pytest.raises(premium.PremiumError, match="not a subset"):
        premium.validate_policy(policy(installed=[], credited=["shutters"]))


# --------------------------------------------------------------------------- #
# A program
# --------------------------------------------------------------------------- #


def test_program_charges_setup_once_and_inspections_per_real_project():
    book = [
        policy("T-1"),
        policy("T-2", installed=["roof_straps"], requested=["roof_straps"], quote=1334.0),  # no-op
        policy("T-3", installed=["roof_straps"], requested=["shutters"], quote=5593.0, rating_value=1_200_000),
    ]
    result = premium.price_program(book, "demo", PLAN, PROGRAM)
    assert result["complete"] and result["project_count"] == 2
    t = result["totals"]
    assert t["project_cost_usd"] == 8234.0 + 5593.0
    assert t["insurer_grants_usd"] == pytest.approx(2058.5 + 1398.25)
    assert t["inspections_usd"] == 200.0 and t["fixed_setup_usd"] == 500.0
    assert t["insurer_upfront_usd"] == pytest.approx(2058.5 + 1398.25 + 200 + 500)
    assert t["homeowner_upfront_usd"] == pytest.approx(8234.0 + 5593.0 - 2058.5 - 1398.25)
    assert t["annual_premium_foregone_usd"] == pytest.approx(1360.0 + 3120.0)


def test_program_with_nothing_selected_has_no_costs_and_no_changes():
    result = premium.price_program([policy("T-1"), policy("T-2")], "demo", PLAN, PROGRAM, selected_proposal_ids=[])
    assert result["project_count"] == 0 and result["selected_proposal_ids"] == []
    t = result["totals"]
    assert t["fixed_setup_usd"] == 0.0 and t["inspections_usd"] == 0.0 and t["insurer_upfront_usd"] == 0.0
    assert t["annual_premium_foregone_usd"] == 0.0
    assert t["current_wind_premium_usd"] == t["result_wind_premium_usd"] == 34_000.0


def test_program_selecting_a_subset_prices_only_those():
    result = premium.price_program([policy("T-1"), policy("T-2")], "demo", PLAN, PROGRAM, selected_proposal_ids=["UPG-T-2"])
    assert result["selected_proposal_ids"] == ["UPG-T-2"] and result["project_count"] == 1
    by_id = {r["policy_id"]: r for r in result["policies"]}
    assert not by_id["T-1"]["selected"] and by_id["T-2"]["selected"]


def test_program_rejects_unknown_proposal_ids():
    with pytest.raises(premium.PremiumError, match="unknown proposal ids"):
        premium.price_program([policy("T-1")], "demo", PLAN, PROGRAM, selected_proposal_ids=["UPG-nope"])


def test_incomplete_program_nulls_cost_totals_but_keeps_premiums():
    book = [policy("T-1"), policy("T-2", installed=["roof_straps"], requested=["roof_straps", "shutters"], quote=7542.0)]
    result = premium.price_program(book, "demo", PLAN, PROGRAM)
    assert not result["complete"]
    assert result["unavailable"] == [{"policy_id": "T-2", "proposal_id": "UPG-T-2", "reason": premium.QUOTE_SCOPE_UNCONFIRMED}]
    assert result["totals"]["project_cost_usd"] is None and result["totals"]["insurer_upfront_usd"] is None
    assert result["totals"]["annual_premium_foregone_usd"] == pytest.approx(1360.0 + 17_000 * 0.13)


# --------------------------------------------------------------------------- #
# The shipped fixtures against the workbook
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def shipped():
    premium.reset_caches()
    return premium.load_credit_plan(), premium.load_policies(), premium.load_insurer_demo()


def test_fixtures_join_every_policy_to_the_demo_portfolio(shipped):
    _, book, demo = shipped
    portfolio = {p["property_id"]: p for p in json.loads((FIXTURES / "example_portfolio.json").read_text())["properties"]}
    assert [p["policy_id"] for p in book["policies"]] == [f"DEMO-P{n:03d}" for n in range(1, 11)]
    for record in book["policies"]:
        assert record["property"] == portfolio[record["property_id"]]
        assert book["frontend_property_map"][str(record["frontend_property_id"])] == record["property_id"]
        assert record["property_id"] == f"P{record['frontend_property_id']:03d}"
    assert demo["insurer"]["insurer_id"] == "insurer-demo-001" and demo["insurer"]["portfolio_id"] == "demo-fl-10"
    assert demo["provenance"]["example_portfolio_sha256"] == book["provenance"]["example_portfolio_sha256"]


def test_normalized_preset_credits_class_inherent_straps_at_baseline(shipped):
    _, book, _ = shipped
    by_id = {p["policy_id"]: p for p in book["policies"]}
    for pid in ("DEMO-P005", "DEMO-P008"):
        assert by_id[pid]["property"]["vulnerability_class"] == "post_fbc_2002"
        assert by_id[pid]["states"]["workbook_reference"]["installed_features"] == []
        assert by_id[pid]["states"]["workbook_reference"]["physical_state_conflict"]
        assert by_id[pid]["states"]["app_consistent_demo"]["installed_features"] == ["roof_straps"]
        assert by_id[pid]["states"]["app_consistent_demo"]["credited_features"] == ["roof_straps"]
    # P002 already had straps in the workbook, so nothing was normalized there.
    assert by_id["DEMO-P002"]["states"]["app_consistent_demo"]["normalization_note"] is None


def test_workbook_reference_reconciles_to_the_cent(shipped):
    plan, book, demo = shipped
    result = premium.price_program(book["policies"], "workbook_reference", plan, demo["program"])
    assert result["complete"] and result["project_count"] == 10
    t = result["totals"]
    assert t["project_cost_usd"] == 68_557.0
    assert t["current_wind_premium_usd"] == 105_895.0
    assert t["result_wind_premium_usd"] == 88_526.6
    assert t["annual_premium_foregone_usd"] == 17_368.4
    assert t["owner_funded_ten_year_undiscounted_premium_net_usd"] == 105_127.0
    assert t == {**t, **{k: v for k, v in demo["reference_totals"]["workbook_reference"].items()}}


def test_app_consistent_demo_reconciles_to_the_cent(shipped):
    plan, book, demo = shipped
    result = premium.price_program(book["policies"], "app_consistent_demo", plan, demo["program"])
    assert result["complete"] and result["project_count"] == 9
    t = result["totals"]
    assert t["project_cost_usd"] == 67_223.0
    assert t["current_wind_premium_usd"] == 104_436.4
    assert t["result_wind_premium_usd"] == 87_476.85
    assert t["annual_premium_foregone_usd"] == 16_959.55
    assert t["insurer_grants_usd"] == 15_418.0
    assert t["inspections_usd"] == 900.0 and t["fixed_setup_usd"] == 500.0
    assert t["insurer_upfront_usd"] == 16_818.0
    assert t["homeowner_upfront_usd"] == 51_805.0
    assert t["owner_funded_ten_year_undiscounted_premium_net_usd"] == 102_372.5
    assert t == {**t, **demo["reference_totals"]["app_consistent_demo"]}


def test_p008_stays_in_the_book_with_zero_project_spending(shipped):
    plan, book, demo = shipped
    result = premium.price_program(book["policies"], "app_consistent_demo", plan, demo["program"])
    row = next(r for r in result["policies"] if r["policy_id"] == "DEMO-P008")
    assert not row["is_project"] and row["features_added"] == []
    assert row["effective_cost_usd"] == 0.0 and row["insurer_grant_usd"] == 0.0
    assert row["current_wind_premium_usd"] == row["result_wind_premium_usd"] == 5_262.4
    assert row["quote_usd"] == 1_334.0  # provenance kept, never charged


def test_per_policy_outputs_match_the_specification_seed(shipped):
    plan, book, demo = shipped
    extract = json.loads(EXTRACT.read_text())
    result = premium.price_program(book["policies"], "app_consistent_demo", plan, demo["program"])
    by_id = {r["policy_id"]: r for r in result["policies"]}
    for entry in extract["policies"]:
        row, ref = by_id[entry["policy_id"]], entry["reference_outputs"]
        assert row["current_wind_premium_usd"] == pytest.approx(ref["current_wind_premium_usd"], abs=0.005)
        assert row["result_wind_premium_usd"] == pytest.approx(ref["result_wind_premium_usd"], abs=0.005)
        assert row["annual_premium_foregone_usd"] == pytest.approx(ref["annual_premium_foregone_usd"], abs=0.005)
        assert row["insurer_grant_usd"] == pytest.approx(ref["insurer_grant_usd"], abs=0.005)
        assert row["homeowner_upfront_usd"] == pytest.approx(ref["homeowner_upfront_usd"], abs=0.005)


def test_homeowner_funded_and_co_funded_arms_differ_only_in_cash(shipped):
    plan, book, demo = shipped
    owner = premium.price_program(book["policies"], "app_consistent_demo", plan, {**demo["program"], "grant_share": 0.0, "grant_cap_usd": 0.0})
    cofunded = premium.price_program(book["policies"], "app_consistent_demo", plan, demo["program"])
    assert owner["totals"]["annual_premium_foregone_usd"] == cofunded["totals"]["annual_premium_foregone_usd"]
    assert owner["totals"]["project_cost_usd"] == cofunded["totals"]["project_cost_usd"] == 67_223.0
    assert owner["totals"]["insurer_grants_usd"] == 0.0 and owner["totals"]["homeowner_upfront_usd"] == 67_223.0
    assert cofunded["totals"]["homeowner_upfront_usd"] + cofunded["totals"]["insurer_grants_usd"] == pytest.approx(67_223.0)
    for a, b in zip(owner["policies"], cofunded["policies"]):
        assert a["resulting_features"] == b["resulting_features"] and a["result_wind_premium_usd"] == b["result_wind_premium_usd"]
