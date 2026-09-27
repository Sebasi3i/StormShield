"""State-transition pricing: the specification's arithmetic, the state-to-curve mapping,
and the shipped package curve.

The arithmetic tests use a tiny inline curve set with explicit `features`, so a change
to the shipped Hazus curves cannot quietly rewrite them. The fixture tests then hold the
shipped curve set to the package's constraints and run the sample insurer's normalized
book through the real wind step for one catalog storm.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import pytest

from app import claims, mitigation_states as ms, premium, wind

METRIC = "peak_3s_gust_10m_open_terrain_mph"

# --------------------------------------------------------------------------- #
# Inline curve set: pre class with four states, post class with two
# --------------------------------------------------------------------------- #


def curve(vclass: str, upgrade: str, features: tuple[str, ...], at_100: float, roof: str = "blended") -> claims.Curve:
    return claims.Curve(
        curve_id=f"fx.{vclass}.{upgrade}.{roof}",
        vulnerability_class=vclass,
        upgrade_id=upgrade,
        wind_metric=METRIC,
        points=((0.0, 0.0), (50.0, 0.0), (100.0, at_100), (200.0, 1.0)),
        evidence_status="assumed",
        source_note="synthetic",
        roof_shape=roof,
        features=features,
    )


PRE_BASE = curve("pre", "baseline", (), 0.375)
PRE_SHUT = curve("pre", "shutters", ("shutters",), 0.25)
PRE_STRAP = curve("pre", "roof_straps", ("roof_straps",), 0.30)
PRE_BOTH = curve("pre", "shutters_roof_straps", ("roof_straps", "shutters"), 0.20)
POST_BASE = curve("post", "baseline", ("roof_straps",), 0.15)
POST_SHUT = curve("post", "shutters", ("roof_straps", "shutters"), 0.10)
PRE_BASE_HIP = curve("pre", "baseline", (), 0.30, roof="hip")

CURVES = {(c.vulnerability_class, c.upgrade_id, c.roof_shape): c for c in (PRE_BASE, PRE_SHUT, PRE_STRAP, PRE_BOTH, POST_BASE, POST_SHUT, PRE_BASE_HIP)}
CURVE_SET = {"curve_set_id": "fx", "wind_metric": METRIC, "evidence_status": "assumed", "provenance": {}, "curves": CURVES}


def state(pid="P1", vclass="pre", current=(), resulting=("shutters",), value=400_000.0, roof="unknown"):
    return ms.PropertyState(pid, value, vclass, roof, premium.canonical_features(current), premium.canonical_features(resulting))


def policy(pid="P1", coverage=400_000.0, pct=0.05):
    return claims.policy_from_template(pid, coverage, {"deductible": {"percent": pct, "basis": "percent_of_coverage_a"}})


def exposure(pid="P1", gust=100.0, storm="S1"):
    return claims.WindExposure(storm, pid, gust, METRIC)


def run(states, policies=None, exposures=None, storms=("S1",)):
    policies = policies or [policy(s.property_id, s.replacement_cost_usd) for s in states]
    exposures = exposures or [exposure(s.property_id) for s in states]
    return ms.compute_transitions(states, policies, exposures, list(storms), curve_set=CURVE_SET)


# --------------------------------------------------------------------------- #
# State -> curve
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "vclass, features, expected",
    [
        ("pre", (), PRE_BASE),
        ("pre", ("shutters",), PRE_SHUT),
        ("pre", ("roof_straps",), PRE_STRAP),
        ("pre", ("shutters", "roof_straps"), PRE_BOTH),
        ("post", ("roof_straps",), POST_BASE),
        ("post", ("roof_straps", "shutters"), POST_SHUT),
    ],
)
def test_state_maps_to_the_curve_whose_building_has_those_features(vclass, features, expected):
    chosen, fallback = ms.curve_for_state(CURVES, vclass, features, "unknown")
    assert chosen is expected and fallback is None


def test_post_2002_without_straps_is_a_conflict_not_a_substitution():
    with pytest.raises(ms.PhysicalStateError, match="conflicts with the class archetype"):
        ms.curve_for_state(CURVES, "post", (), "unknown")
    with pytest.raises(ms.PhysicalStateError):
        ms.curve_for_state(CURVES, "post", ("shutters",), "unknown")


def test_declared_roof_shape_selects_its_curve_and_falls_back_with_a_reason():
    chosen, fallback = ms.curve_for_state(CURVES, "pre", (), "hip")
    assert chosen is PRE_BASE_HIP and fallback is None
    chosen, fallback = ms.curve_for_state(CURVES, "pre", ("shutters",), "hip")
    assert chosen is PRE_SHUT and "used the blended curve" in fallback


def test_curve_set_without_features_cannot_price_states():
    bare = {("pre", "baseline", "blended"): claims.Curve("bare", "pre", "baseline", METRIC, ((0.0, 0.0), (100.0, 0.1)), "assumed", "x")}
    with pytest.raises(ms.PhysicalStateError, match="records no 'features'"):
        ms.curve_for_state(bare, "pre", (), "unknown")


# --------------------------------------------------------------------------- #
# The specification's arithmetic
# --------------------------------------------------------------------------- #


def test_pure_arithmetic_example_from_the_specification():
    """$400,000 home and Coverage A, 5% deductible, fractions 0.375 -> 0.25."""
    row = run([state()])["rows"][0]
    assert row["deductible_usd"] == 20_000.0 and row["coverage_limit_usd"] == 400_000.0
    assert row["current_damage_usd"] == 150_000.0 and row["result_damage_usd"] == 100_000.0
    assert row["current_payout_usd"] == 130_000.0 and row["result_payout_usd"] == 80_000.0
    assert row["avoided_payout_usd"] == 50_000.0 and row["avoided_damage_usd"] == 50_000.0
    assert row["current_uninsured_damage_usd"] == 20_000.0 and row["result_uninsured_damage_usd"] == 20_000.0
    assert row["avoided_uninsured_damage_usd"] == 0.0


def test_below_deductible_damage_pays_nothing_and_is_all_uninsured():
    # 10 mph above the curve's first nonzero point: fraction 0.0375 x 400k = 15k < 20k deductible
    row = run([state()], exposures=[exposure(gust=55.0)])["rows"][0]
    assert 0 < row["current_damage_usd"] < 20_000
    assert row["current_payout_usd"] == 0.0 and row["result_payout_usd"] == 0.0
    assert row["current_uninsured_damage_usd"] == row["current_damage_usd"]
    assert row["avoided_payout_usd"] == 0.0 and row["avoided_damage_usd"] > 0


def test_loss_above_the_limit_is_capped_at_the_limit():
    row = run([state()], policies=[policy(coverage=400_000.0, pct=0.0)], exposures=[exposure(gust=200.0)])["rows"][0]
    assert row["current_damage_usd"] == 400_000.0 and row["current_payout_usd"] == 400_000.0
    low_limit = claims.Policy("P1", 0.0, 100_000.0)
    row = run([state()], policies=[low_limit], exposures=[exposure(gust=200.0)])["rows"][0]
    assert row["current_payout_usd"] == 100_000.0 and row["current_uninsured_damage_usd"] == 300_000.0


def test_identical_states_produce_a_no_op_row_with_zero_deltas():
    result = run([state(current=("roof_straps",), resulting=("roof_straps",))])
    row = result["rows"][0]
    assert row["no_op"] and result["curve_selection"][0]["no_op"]
    assert row["current_curve_id"] == row["result_curve_id"] == PRE_STRAP.curve_id
    assert row["avoided_damage_usd"] == row["avoided_payout_usd"] == row["avoided_uninsured_damage_usd"] == 0.0
    assert row["current_payout_usd"] == 100_000.0  # 0.30 x 400k - 20k, so not "nothing happened"


def test_straps_to_both_compares_the_straps_curve_not_the_baseline():
    row = run([state(current=("roof_straps",), resulting=("roof_straps", "shutters"))])["rows"][0]
    assert row["current_curve_id"] == PRE_STRAP.curve_id and row["result_curve_id"] == PRE_BOTH.curve_id
    assert row["current_damage_usd"] == 120_000.0 and row["result_damage_usd"] == 80_000.0
    assert row["avoided_payout_usd"] == 40_000.0  # (120k - 20k) - (80k - 20k), not 150k-based


def test_deductible_change_moves_loss_between_payout_and_uninsured_without_changing_damage():
    five = run([state()], policies=[policy(pct=0.05)])["rows"][0]
    ten = run([state()], policies=[policy(pct=0.10)])["rows"][0]
    assert five["current_damage_usd"] == ten["current_damage_usd"] == 150_000.0
    assert ten["current_payout_usd"] == 110_000.0 and ten["current_uninsured_damage_usd"] == 40_000.0
    assert five["current_payout_usd"] + five["current_uninsured_damage_usd"] == ten["current_payout_usd"] + ten["current_uninsured_damage_usd"]


def test_baseline_matches_claims_engine_for_the_same_state_and_wind():
    """The map's baseline (claims.compute_losses) and this module's current state agree
    to the cent, because both call the same arithmetic on the same curve."""
    result = run([state(value=987_654.0)], exposures=[exposure(gust=137.3)])
    row = result["rows"][0]
    reference = claims.compute_losses(
        [claims.Property("P1", 987_654.0, "pre")], [policy("P1", 987_654.0)], [exposure(gust=137.3)], ["S1"],
        run_id="t", catalog_id="t", sampling_description="t", curve_set=CURVE_SET,
    )["rows"]
    shutters = next(r for r in reference if r["upgrade_id"] == "shutters")
    assert row["current_damage_usd"] == shutters["baseline_damage_usd"]
    assert row["current_payout_usd"] == shutters["baseline_payout_usd"]
    assert row["result_payout_usd"] == shutters["upgraded_payout_usd"]


def test_every_declared_storm_and_property_gets_exactly_one_row():
    states = [state("P1"), state("P2", current=("shutters",), resulting=("shutters",)), state("P3", vclass="post", current=("roof_straps",), resulting=("roof_straps", "shutters"))]
    exposures = [exposure(p, g, s) for s in ("S1", "S2") for p, g in (("P1", 100.0), ("P2", 100.0), ("P3", 60.0))]
    result = run(states, exposures=exposures, storms=("S1", "S2"))
    assert [(r["storm_id"], r["property_id"]) for r in result["rows"]] == [(s, p) for s in ("S1", "S2") for p in ("P1", "P2", "P3")]
    assert result["totals_by_storm"]["S1"]["current_payout_usd"] == 130_000.0 + 80_000.0 + 0.0
    assert result["totals_by_storm"]["S1"]["avoided_payout_usd"] == 50_000.0


def test_missing_exposure_is_an_error_not_zero():
    with pytest.raises(claims.MissingDataError, match="no wind exposure"):
        run([state("P1"), state("P2")], exposures=[exposure("P1")])


def test_wind_metric_mismatch_is_rejected():
    with pytest.raises(claims.UnitMismatchError):
        run([state()], exposures=[claims.WindExposure("S1", "P1", 100.0, "sustained_1min_mph")])


def test_unrecognised_roof_shape_warns_and_uses_blended():
    result = run([state(roof="mansard")])
    assert any("not a recognised value" in w for w in result["warnings"])
    assert result["curve_selection"][0]["resolved_roof_shape"] == "blended"


# --------------------------------------------------------------------------- #
# Building inputs from the insurer fixture
# --------------------------------------------------------------------------- #


def test_state_from_record_uses_the_preset_and_the_selection():
    premium.reset_caches()
    by_id = {p["policy_id"]: p for p in premium.load_policies()["policies"]}
    p002 = ms.state_from_record(by_id["DEMO-P002"], "app_consistent_demo")
    assert p002.current_features == ("roof_straps",) and p002.resulting_features == ("roof_straps", "shutters")
    assert p002.vulnerability_class == "post_fbc_2002" and p002.roof_shape == "gable"
    p008 = ms.state_from_record(by_id["DEMO-P008"], "app_consistent_demo")
    assert p008.current_features == p008.resulting_features == ("roof_straps",)  # no-op
    unselected = ms.state_from_record(by_id["DEMO-P001"], "app_consistent_demo", selected=False)
    assert unselected.current_features == unselected.resulting_features == ()
    pol = ms.policy_from_record(by_id["DEMO-P001"])
    assert pol.deductible_usd == 42_500.0 and pol.coverage_limit_usd == 850_000.0 and pol.deductible_percent == 0.05


def test_workbook_reference_preset_cannot_be_priced_physically():
    premium.reset_caches()
    by_id = {p["policy_id"]: p for p in premium.load_policies()["policies"]}
    claims.reset_caches()
    s = ms.state_from_record(by_id["DEMO-P005"], "workbook_reference")
    with pytest.raises(ms.PhysicalStateError, match="post_fbc_2002"):
        ms.compute_transitions([s], [ms.policy_from_record(by_id["DEMO-P005"])], [exposure("P005")], ["S1"])


# --------------------------------------------------------------------------- #
# The shipped curve set
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def shipped():
    claims.reset_caches()
    return claims.load_curve_set()


def test_shipped_curve_set_has_the_package_for_every_roof_shape(shipped):
    curves = shipped["curves"]
    assert len(curves) == 18
    assert shipped["curve_set_id"] == "hazus-msf1-suburban-v3"
    for shape in ("gable", "hip", "blended"):
        both = curves[("pre_fbc_2002", "shutters_roof_straps", shape)]
        assert both.features == ("roof_straps", "shutters")
        assert curves[("pre_fbc_2002", "baseline", shape)].features == ()
        assert curves[("post_fbc_2002", "baseline", shape)].features == ("roof_straps",)
        assert curves[("post_fbc_2002", "shutters", shape)].features == ("roof_straps", "shutters")
    assert claims.eligible_upgrades(curves, "pre_fbc_2002") == ["roof_straps", "shutters", "shutters_roof_straps"]
    assert claims.eligible_upgrades(curves, "post_fbc_2002") == ["shutters"]


def test_shipped_package_never_does_worse_than_either_single_feature(shipped):
    curves = shipped["curves"]
    for shape in ("gable", "hip", "blended"):
        both = curves[("pre_fbc_2002", "shutters_roof_straps", shape)]
        shutters = curves[("pre_fbc_2002", "shutters", shape)]
        straps = curves[("pre_fbc_2002", "roof_straps", shape)]
        for mph in range(0, 251, 5):
            f = claims.damage_fraction(both, mph)
            assert f <= claims.damage_fraction(shutters, mph) + 1e-12
            assert f <= claims.damage_fraction(straps, mph) + 1e-12
        # and strictly better than either somewhere in the damaging range
        assert claims.damage_fraction(both, 140) < min(claims.damage_fraction(shutters, 140), claims.damage_fraction(straps, 140))
    detail = shipped["provenance"]["ordering_adjustment_detail"]
    assert {d["capped_to"] for d in detail if d["upgrade_id"] == "shutters_roof_straps"} == {"pre_fbc_2002.shutters", "pre_fbc_2002.roof_straps", "pre_fbc_2002.baseline"}


def test_every_shipped_physical_state_resolves(shipped):
    curves = shipped["curves"]
    for vclass, features in (
        ("pre_fbc_2002", ()), ("pre_fbc_2002", ("shutters",)), ("pre_fbc_2002", ("roof_straps",)),
        ("pre_fbc_2002", ("roof_straps", "shutters")), ("post_fbc_2002", ("roof_straps",)), ("post_fbc_2002", ("roof_straps", "shutters")),
    ):
        for roof in ("gable", "hip", "unknown"):
            chosen, fallback = ms.curve_for_state(curves, vclass, features, roof)
            assert fallback is None and chosen.vulnerability_class == vclass and chosen.features == features


def test_normalized_book_on_the_catalog_storm_prices_one_pair_per_policy():
    """The sample insurer's default preset through the real wind step for SYN0155."""
    premium.reset_caches()
    claims.reset_caches()
    book = premium.load_policies()["policies"]
    states = [ms.state_from_record(r, "app_consistent_demo") for r in book]
    policies = [ms.policy_from_record(r) for r in book]
    storm = wind.storm_by_id("SYN0155")
    exposures, _ = wind.exposures_for_storm(storm, [(s.property_id, r["property"]["latitude"], r["property"]["longitude"]) for s, r in zip(states, book)])
    result = ms.compute_transitions(states, policies, exposures, ["SYN0155"])

    assert len(result["rows"]) == 10 and result["warnings"] == []
    by_id = {r["property_id"]: r for r in result["rows"]}
    assert by_id["P008"]["no_op"] and by_id["P008"]["avoided_payout_usd"] == 0.0
    assert by_id["P002"]["current_curve_id"] == "post_fbc_2002.baseline.gable.hazus.v1"
    assert by_id["P002"]["result_curve_id"] == "post_fbc_2002.shutters.gable.hazus.v1"
    assert by_id["P003"]["result_curve_id"] == "pre_fbc_2002.shutters_roof_straps.hip.hazus.v1"
    totals = result["totals_by_storm"]["SYN0155"]
    assert totals["current_payout_usd"] == pytest.approx(sum(r["current_payout_usd"] for r in result["rows"]), abs=0.05)
    assert 0 < totals["result_payout_usd"] < totals["current_payout_usd"]
    assert totals["avoided_payout_usd"] == pytest.approx(totals["current_payout_usd"] - totals["result_payout_usd"], abs=0.05)
    # The Miami Cat 4 does most of the work, as the audit found: over a million in
    # current payout on this book, most of it avoidable with the package.
    assert totals["current_payout_usd"] > 1_000_000
    assert totals["avoided_payout_usd"] > 700_000
