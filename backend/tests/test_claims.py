"""The worked checks from the CS Developer 1 brief, plus the rejections it requires.

Every dollar figure here is synthetic. The fixture curve is the brief's own tiny
fixture - baseline 0.10 and upgraded 0.04 at 100 mph - and deliberately not the
production curve set, so a change to the shipped curves cannot quietly rewrite the
arithmetic these tests are meant to pin down.

Run from the backend directory:

    python -m pytest tests -q
"""

from __future__ import annotations

import pytest

from app import claims


# --------------------------------------------------------------------------- #
# The brief's tiny fixture
# --------------------------------------------------------------------------- #

METRIC = "peak_3s_gust_10m_open_terrain_mph"

REPLACEMENT_COST = 500_000.0
DEDUCTIBLE = 8_000.0
COVERAGE_LIMIT = 400_000.0


def curve(upgrade_id: str, damage_at_100: float) -> claims.Curve:
    return claims.Curve(
        curve_id=f"fixture.{upgrade_id}",
        vulnerability_class="fixture_class",
        upgrade_id=upgrade_id,
        wind_metric=METRIC,
        points=((0.0, 0.0), (75.0, 0.0), (100.0, damage_at_100), (200.0, 1.0)),
        evidence_status="assumed",
        source_note="Synthetic fixture for tests. Not a model input.",
    )


BASELINE_CURVE = curve("baseline", 0.10)
SHUTTERS_CURVE = curve("shutters", 0.04)

CURVE_SET = {
    "curve_set_id": "test-fixture",
    "wind_metric": METRIC,
    "evidence_status": "assumed",
    "provenance": {"summary": "synthetic test fixture"},
    "curves": {
        ("fixture_class", "baseline"): BASELINE_CURVE,
        ("fixture_class", "shutters"): SHUTTERS_CURVE,
    },
}


def run(peak_gust_mph: float, **kwargs) -> dict:
    """One storm, one property, one upgrade, at the given gust."""
    return claims.compute_losses(
        [claims.Property("P001", REPLACEMENT_COST, "fixture_class")],
        [claims.Policy("P001", DEDUCTIBLE, COVERAGE_LIMIT)],
        [claims.WindExposure("S001", "P001", peak_gust_mph, METRIC)],
        ["S001"],
        run_id="test-001",
        catalog_id="test-catalog",
        sampling_description="synthetic",
        curve_set=CURVE_SET,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# The worked example
# --------------------------------------------------------------------------- #


def test_worked_example_at_100_mph():
    """The brief's headline check: 50k/20k damage, 42k/12k payout, 30k avoided."""
    row = run(100.0)["rows"][0]

    assert row["baseline_damage_usd"] == 50_000
    assert row["upgraded_damage_usd"] == 20_000
    assert row["baseline_payout_usd"] == 42_000
    assert row["upgraded_payout_usd"] == 12_000
    assert row["avoided_payout_usd"] == 30_000


def test_damage_below_deductible_pays_nothing():
    assert claims.payout_usd(5_000, DEDUCTIBLE, COVERAGE_LIMIT) == 0


def test_damage_exactly_at_deductible_pays_nothing():
    assert claims.payout_usd(8_000, DEDUCTIBLE, COVERAGE_LIMIT) == 0


def test_payout_is_capped_at_the_coverage_limit():
    """A total loss of the 500k building pays the 400k limit, not 492k."""
    assert claims.payout_usd(500_000, DEDUCTIBLE, COVERAGE_LIMIT) == 400_000


def test_zero_wind_is_zero_damage_and_zero_payout():
    row = run(0.0)["rows"][0]

    assert row["baseline_damage_usd"] == 0
    assert row["baseline_payout_usd"] == 0
    assert row["avoided_payout_usd"] == 0


def test_baseline_is_identical_across_upgrade_rows():
    """Two upgrades on one property must not disagree about the baseline."""
    curves = dict(CURVE_SET["curves"])
    curves[("fixture_class", "roof_straps")] = curve("roof_straps", 0.06)
    curve_set = {**CURVE_SET, "curves": curves}

    result = claims.compute_losses(
        [claims.Property("P001", REPLACEMENT_COST, "fixture_class")],
        [claims.Policy("P001", DEDUCTIBLE, COVERAGE_LIMIT)],
        [claims.WindExposure("S001", "P001", 100.0, METRIC)],
        ["S001"],
        run_id="test-002",
        catalog_id="test-catalog",
        sampling_description="synthetic",
        curve_set=curve_set,
    )

    rows = result["rows"]
    assert len(rows) == 2
    assert {r["upgrade_id"] for r in rows} == {"shutters", "roof_straps"}
    assert len({r["baseline_damage_usd"] for r in rows}) == 1
    assert len({r["baseline_payout_usd"] for r in rows}) == 1


# --------------------------------------------------------------------------- #
# Interpolation
# --------------------------------------------------------------------------- #


def test_interpolation_is_linear_between_points():
    """Halfway between (100, 0.10) and (200, 1.0) is 0.55."""
    assert claims.damage_fraction(BASELINE_CURVE, 150.0) == pytest.approx(0.55)


def test_interpolation_hits_the_declared_points_exactly():
    assert claims.damage_fraction(BASELINE_CURVE, 100.0) == pytest.approx(0.10)
    assert claims.damage_fraction(BASELINE_CURVE, 75.0) == pytest.approx(0.0)


def test_wind_above_the_curve_is_rejected_not_extrapolated():
    with pytest.raises(claims.UnsupportedInputError):
        claims.damage_fraction(BASELINE_CURVE, 260.0)


def test_negative_wind_is_rejected():
    with pytest.raises(claims.EngineError):
        claims.damage_fraction(BASELINE_CURVE, -5.0)


def test_mismatched_wind_metric_is_rejected_not_converted():
    with pytest.raises(claims.UnitMismatchError):
        claims.damage_fraction(BASELINE_CURVE, 100.0, "1min_sustained_kt")


# --------------------------------------------------------------------------- #
# Curve validation
# --------------------------------------------------------------------------- #


def test_curve_without_a_zero_origin_is_rejected():
    bad = BASELINE_CURVE._replace(points=((50.0, 0.01), (100.0, 0.10)))
    with pytest.raises(claims.CurveError):
        claims.validate_curve(bad)


def test_curve_with_decreasing_damage_is_rejected():
    bad = BASELINE_CURVE._replace(points=((0.0, 0.0), (100.0, 0.10), (150.0, 0.05)))
    with pytest.raises(claims.CurveError):
        claims.validate_curve(bad)


def test_curve_with_non_increasing_wind_is_rejected():
    bad = BASELINE_CURVE._replace(points=((0.0, 0.0), (100.0, 0.10), (100.0, 0.20)))
    with pytest.raises(claims.CurveError):
        claims.validate_curve(bad)


def test_curve_with_fraction_above_one_is_rejected():
    bad = BASELINE_CURVE._replace(points=((0.0, 0.0), (100.0, 1.4)))
    with pytest.raises(claims.CurveError):
        claims.validate_curve(bad)


def test_shipped_curve_set_validates():
    """The production fixture must satisfy the same rules as any supplied curve."""
    claims.reset_caches()
    curve_set = claims.load_curve_set()

    assert curve_set["curves"], "no curves loaded"
    for shipped in curve_set["curves"].values():
        claims.validate_curve(shipped)
        assert shipped.wind_metric == curve_set["wind_metric"]
    # Every class must have a baseline, or nothing can be compared against.
    classes = {c.vulnerability_class for c in curve_set["curves"].values()}
    for name in classes:
        assert (name, "baseline") in curve_set["curves"]


def test_shipped_curves_are_reproducible_from_hazus():
    """damage_curves.json is exactly what scripts/build_damage_curves.py derives from the
    committed Hazus extract, so the curves cannot drift from their source unnoticed."""
    import importlib.util
    import json
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("build_damage_curves", backend / "scripts" / "build_damage_curves.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    committed = json.loads((backend / "app" / "fixtures" / "damage_curves.json").read_text(encoding="utf-8"))
    assert script.build_curve_set() == committed


def test_shipped_upgrades_never_add_damage():
    """An upgrade curve must sit at or below its class baseline at every speed, and the
    post-2002 baseline at or below the pre-2002 one; otherwise an avoided payout could go
    negative for a reason the source does not support."""
    import numpy as np

    claims.reset_caches()
    curves = claims.load_curve_set()["curves"]
    grid = np.arange(0, 251, 5)

    def at(key):
        winds, damage = zip(*curves[key].points)
        return np.interp(grid, winds, damage)

    for (vclass, upgrade) in curves:
        if upgrade != "baseline":
            assert (at((vclass, upgrade)) <= at((vclass, "baseline")) + 1e-9).all(), (vclass, upgrade)
    assert (at(("post_fbc_2002", "baseline")) <= at(("pre_fbc_2002", "baseline")) + 1e-9).all()


# --------------------------------------------------------------------------- #
# Validation errors the brief asks for by name
# --------------------------------------------------------------------------- #


def test_missing_curve_is_an_actionable_error():
    with pytest.raises(claims.MissingDataError) as raised:
        claims.compute_losses(
            [claims.Property("P001", REPLACEMENT_COST, "class_with_no_curves")],
            [claims.Policy("P001", DEDUCTIBLE, COVERAGE_LIMIT)],
            [claims.WindExposure("S001", "P001", 100.0, METRIC)],
            ["S001"],
            run_id="test-003",
            catalog_id="test-catalog",
            sampling_description="synthetic",
            eligible_options={"P001": ["shutters"]},
            curve_set=CURVE_SET,
        )

    assert "class_with_no_curves" in str(raised.value)


def test_missing_wind_exposure_is_an_error_not_a_zero():
    """The distinction the brief insists on: absent row means missing data."""
    with pytest.raises(claims.MissingDataError) as raised:
        claims.compute_losses(
            [claims.Property("P001", REPLACEMENT_COST, "fixture_class")],
            [claims.Policy("P001", DEDUCTIBLE, COVERAGE_LIMIT)],
            [],
            ["S001"],
            run_id="test-004",
            catalog_id="test-catalog",
            sampling_description="synthetic",
            curve_set=CURVE_SET,
        )

    assert "S001" in str(raised.value) and "P001" in str(raised.value)


def test_duplicate_exposure_row_is_rejected():
    with pytest.raises(claims.DuplicateRowError):
        claims.compute_losses(
            [claims.Property("P001", REPLACEMENT_COST, "fixture_class")],
            [claims.Policy("P001", DEDUCTIBLE, COVERAGE_LIMIT)],
            [
                claims.WindExposure("S001", "P001", 100.0, METRIC),
                claims.WindExposure("S001", "P001", 120.0, METRIC),
            ],
            ["S001"],
            run_id="test-005",
            catalog_id="test-catalog",
            sampling_description="synthetic",
            curve_set=CURVE_SET,
        )


def test_missing_policy_is_rejected():
    with pytest.raises(claims.MissingDataError):
        claims.compute_losses(
            [claims.Property("P001", REPLACEMENT_COST, "fixture_class")],
            [],
            [claims.WindExposure("S001", "P001", 100.0, METRIC)],
            ["S001"],
            run_id="test-006",
            catalog_id="test-catalog",
            sampling_description="synthetic",
            curve_set=CURVE_SET,
        )


def test_negative_replacement_cost_is_rejected():
    with pytest.raises(claims.EngineError):
        claims.damage_usd(-1.0, 0.1)


def test_baseline_is_not_an_upgrade_option():
    with pytest.raises(claims.EngineError):
        run(100.0, eligible_options={"P001": ["baseline"]})


# --------------------------------------------------------------------------- #
# Deductible handling
# --------------------------------------------------------------------------- #


def test_percentage_deductible_uses_the_policy_base_not_the_damage():
    """5% of a 400k dwelling limit is 20k, whatever the damage happens to be."""
    assert claims.deductible_from_percent(0.05, 400_000) == 20_000


def test_workbook_worked_example_reproduces():
    """The Finance workbook: 150k damage, 400k Coverage A, 5% deductible, pays 130k."""
    deductible = claims.deductible_from_percent(0.05, 400_000)

    assert claims.payout_usd(150_000, deductible, 400_000) == 130_000


def test_policy_from_template_scales_with_the_home():
    policy = claims.policy_from_template("P001", 850_000)

    assert policy.deductible_usd == 42_500
    assert policy.coverage_limit_usd == 850_000
    assert policy.deductible_base_usd == 850_000
    assert policy.deductible_percent == 0.05


def test_percent_outside_zero_to_one_is_rejected():
    with pytest.raises(claims.EngineError):
        claims.deductible_from_percent(5.0, 400_000)


# --------------------------------------------------------------------------- #
# Negative avoided payouts are preserved, not hidden
# --------------------------------------------------------------------------- #


def test_negative_avoided_payout_is_preserved_and_warned():
    """An upgrade curve worse than baseline is a data problem worth surfacing."""
    curves = dict(CURVE_SET["curves"])
    curves[("fixture_class", "shutters")] = curve("shutters", 0.20)
    result = claims.compute_losses(
        [claims.Property("P001", REPLACEMENT_COST, "fixture_class")],
        [claims.Policy("P001", DEDUCTIBLE, COVERAGE_LIMIT)],
        [claims.WindExposure("S001", "P001", 100.0, METRIC)],
        ["S001"],
        run_id="test-007",
        catalog_id="test-catalog",
        sampling_description="synthetic",
        curve_set={**CURVE_SET, "curves": curves},
    )

    row = result["rows"][0]
    assert row["avoided_payout_usd"] == -50_000
    assert any("negative avoided payout" in w for w in result["warnings"])


# --------------------------------------------------------------------------- #
# Envelope completeness
# --------------------------------------------------------------------------- #


def test_envelope_declares_everything_needed_to_check_completeness():
    result = run(100.0)

    assert result["schema_version"] == "1.1"
    assert result["storm_ids"] == ["S001"]
    assert result["property_ids"] == ["P001"]
    assert result["eligible_options"] == [{"property_id": "P001", "upgrade_ids": ["shutters"]}]
    assert result["evidence_status"] == "assumed"
    assert len(result["rows"]) == 1
    assert result["assumptions"], "assumptions must never be empty"


def test_every_storm_property_upgrade_combination_gets_a_row():
    """Including a storm that misses: an explicit zero row, never an absent one."""
    properties = [
        claims.Property("P001", REPLACEMENT_COST, "fixture_class"),
        claims.Property("P002", REPLACEMENT_COST, "fixture_class"),
    ]
    policies = [
        claims.Policy("P001", DEDUCTIBLE, COVERAGE_LIMIT),
        claims.Policy("P002", DEDUCTIBLE, COVERAGE_LIMIT),
    ]
    exposures = [
        claims.WindExposure("S001", "P001", 100.0, METRIC),
        claims.WindExposure("S001", "P002", 0.0, METRIC),
        claims.WindExposure("S002", "P001", 0.0, METRIC),
        claims.WindExposure("S002", "P002", 120.0, METRIC),
    ]

    result = claims.compute_losses(
        properties,
        policies,
        exposures,
        ["S001", "S002"],
        run_id="test-008",
        catalog_id="test-catalog",
        sampling_description="synthetic",
        curve_set=CURVE_SET,
    )

    # 2 storms x 2 properties x 1 eligible upgrade.
    assert len(result["rows"]) == 4
    missed = next(
        r for r in result["rows"] if r["storm_id"] == "S001" and r["property_id"] == "P002"
    )
    assert missed["baseline_damage_usd"] == 0
    assert missed["baseline_payout_usd"] == 0


def test_no_annual_probability_is_attached_anywhere():
    """Annual rates belong to the simulator owner and the consuming module, not here."""
    result = run(100.0)
    text = repr(result).lower()

    for forbidden in ("annual_probability", "return_period", "exceedance", "aal"):
        assert forbidden not in text
