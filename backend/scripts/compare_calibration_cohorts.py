"""Sensitivity and payout comparison across the wind-calibration storm cohorts.

Builds the primary (2016-2024) and combined (2004-2024) bundles in memory - never
from a production fixture - and a third, explicitly diagnostic bundle that swaps only
the gust factor between them, then asks two separate questions with two separate
sections:

  - wind_accuracy: how well does each bundle reproduce station peak gusts, scored on
    exactly the same eligible station-storm keys for the primary, legacy and combined
    storm sets? (scripts/validate_wind_field.py, called once per bundle against all 19
    storms and subset by cohort membership afterwards, so the wind field is never
    recomputed for a storm it has already scored.)
  - holdout: each cohort's own leave-one-storm-out result, republished verbatim from
    scripts/calibrate_wind_field.py's cross_validation - never recomputed here, and
    never conflated with the fixed-bundle comparison above, which scores one already-
    chosen bundle rather than refitting per fold.
  - payout_comparison: the same three scenarios run through the actual claims engine
    against three hand-picked catalog storms (SYN0155, SYN0697, SYN0973) and the demo
    portfolio, with every non-wind input - the portfolio's seeded roof-shape
    assignment, the damage curve_set_id, the policy template, eligible upgrades - held
    byte-for-byte identical across scenarios, so a payout difference is attributable to
    the wind bundle alone (see backend/scripts/assign_demo_roof_shapes.py).

Three scenarios, never a fourth chosen by whichever scores best:

  - primary: the primary cohort's own gust factor and profile fit. Production.
  - factor_only: the combined cohort's gust factor with the primary cohort's decay and
    land factor FROZEN. Diagnostic only - isolates arithmetic sensitivity to the gust
    factor alone, and is not an independently fitted bundle.
  - combined: the combined cohort's own gust factor and profile fit, refit jointly.
    An explicit alternative, never silently promoted.

An optional fourth, current_production, is the bundle exactly as committed before this
comparison's own fits were promoted - captured live from the fixtures on disk, since
this script itself never writes to them. An orchestrator that regenerates fixtures
should capture it BEFORE promoting anything, and pass that snapshot in explicitly
(see build_comparison's current_production_bundle parameter) rather than relying on
this script's own default, which is only correct when nothing has been promoted yet.

The primary cohort is a predetermined policy, not a conclusion of this script: it
reports whether the alternatives score better or worse, and never recommends adopting
one because it happened to.

Writes app/fixtures/calibration_sensitivity.json. Usage, from the backend directory:

    python scripts/compare_calibration_cohorts.py
    python scripts/compare_calibration_cohorts.py --no-current-production
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import claims, wind  # noqa: E402
import calibrate_wind_field as cwf  # noqa: E402
import calibration_common as cc  # noqa: E402
import fit_gust_factor  # noqa: E402
import validate_wind_field as vwf  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES_DIR = BACKEND / "app" / "fixtures"
FIXTURE = FIXTURES_DIR / "calibration_sensitivity.json"
PORTFOLIO_PATH = FIXTURES_DIR / "example_portfolio.json"

CALIBRATION_SENSITIVITY_ID = "fl-asos-cohort-sensitivity-v1"

# Hand-picked catalog storms this comparison prices, not a probabilistic sample -
# see storm_catalog.json's sampling_description for the same caveat on the catalog.
COMPARISON_STORM_IDS = ("SYN0155", "SYN0697", "SYN0973")


# --------------------------------------------------------------------------- #
# Engine inputs held constant across every scenario
# --------------------------------------------------------------------------- #


def _portfolio() -> dict:
    return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))


def _engine_inputs(
    portfolio: dict,
) -> tuple[list[claims.Property], list[claims.Policy], list[tuple[str, float, float]]]:
    """Properties, policies (from the illustrative Finance template) and coordinates -
    identical objects reused for every scenario. Only the wind bundle passed to
    wind.exposures_for_storm ever varies between scenarios in this script."""
    properties, policies, coordinates = [], [], []
    for entry in portfolio["properties"]:
        properties.append(
            claims.Property(
                property_id=entry["property_id"],
                replacement_cost_usd=entry["replacement_cost_usd"],
                vulnerability_class=entry["vulnerability_class"],
                roof_shape=entry.get("roof_shape", "unknown"),
            )
        )
        policies.append(claims.policy_from_template(entry["property_id"], entry["replacement_cost_usd"]))
        coordinates.append((entry["property_id"], entry["latitude"], entry["longitude"]))
    return properties, policies, coordinates


# --------------------------------------------------------------------------- #
# Wind accuracy: one bundle, scored once against all 19 storms, then subset
# --------------------------------------------------------------------------- #


def _rounded_metrics(frame: pd.DataFrame) -> dict:
    """Same rounding scripts/validate_wind_field.py's own summary uses, duplicated
    (not imported) because it is a three-line display concern, not a calculation -
    the actual arithmetic is calibration_common.metrics_from_columns, shared."""
    core = cc.metrics_from_columns(frame)
    if core["n"] == 0:
        return {"n": 0}
    return {
        "n": core["n"],
        "bias_kt": round(core["bias_kt"], 1),
        "mean_absolute_error_kt": round(core["mean_absolute_error_kt"], 1),
        "median_ratio_modeled_over_observed": round(core["median_ratio"], 3),
        "within_15_percent": round(core["within_15_percent"], 2),
        "observed_mean_kt": round(core["observed_mean_kt"], 1),
        "modeled_mean_kt": round(core["modeled_mean_kt"], 1),
    }


def wind_accuracy_for_bundle(bundle: dict) -> dict:
    """This bundle's accuracy against station peak gusts, for the primary, legacy and
    combined storm sets - scored on exactly the same eligible station-storm keys,
    because all three are subsets of one comparison table computed against every storm
    once. Scoring the same bundle against primary/legacy/combined separately would
    recompute the wind field for storms in more than one cohort; this does not."""
    _, table = vwf.validate(bundle, cohort=cc.COMBINED_COHORT)
    primary_ids = cc.cohort_storm_ids(cc.PRIMARY_COHORT)
    legacy_ids = cc.cohort_storm_ids(cc.LEGACY_COHORT)
    return {
        cc.PRIMARY_COHORT: _rounded_metrics(table[table["storm_id"].isin(primary_ids)]),
        cc.LEGACY_COHORT: _rounded_metrics(table[table["storm_id"].isin(legacy_ids)]),
        cc.COMBINED_COHORT: _rounded_metrics(table),
    }


# --------------------------------------------------------------------------- #
# Payout comparison: the actual claims engine, one storm and one bundle at a time
# --------------------------------------------------------------------------- #


def price_storm(
    storm: dict,
    bundle: dict,
    properties: list[claims.Property],
    policies: list[claims.Policy],
    coordinates: list[tuple[str, float, float]],
    curve_set: dict,
) -> dict:
    """Peak gust, baseline payout and avoided payout per property for one storm under
    one wind bundle, with every other input - properties, policies, curve set -
    unchanged from the caller. Baseline payout is computed directly with the same
    helpers compute_losses uses internally (find_curve/damage_fraction/damage_usd/
    payout_usd) rather than read back out of its rows, so a property whose
    vulnerability class happened to have no eligible upgrade would still get a
    baseline figure; avoided payout, which only exists for an eligible upgrade, comes
    from compute_losses's own rows.
    """
    curves = curve_set["curves"]
    policies_by_id = {p.property_id: p for p in policies}
    exposures, _ = wind.exposures_for_storm(storm, coordinates, bundle=bundle)
    exposures_by_property = {e.property_id: e for e in exposures}

    peak_gust_mph: dict[str, float] = {}
    baseline_payout_usd: dict[str, float] = {}
    for prop in properties:
        exposure = exposures_by_property[prop.property_id]
        policy = policies_by_id[prop.property_id]
        peak_gust_mph[prop.property_id] = round(exposure.peak_gust_mph, 1)
        baseline_curve = claims.find_curve(curves, prop.vulnerability_class, claims.BASELINE, prop.roof_shape)
        fraction = claims.damage_fraction(baseline_curve, exposure.peak_gust_mph, exposure.wind_metric)
        damage = claims.damage_usd(prop.replacement_cost_usd, fraction)
        payout = claims.payout_usd(damage, policy.deductible_usd, policy.coverage_limit_usd)
        baseline_payout_usd[prop.property_id] = round(payout, 2)

    result = claims.compute_losses(
        properties,
        policies,
        [
            claims.WindExposure(
                storm_id=storm["storm_id"],
                property_id=exposure.property_id,
                peak_gust_mph=exposure.peak_gust_mph,
                wind_metric=exposure.wind_metric,
            )
            for exposure in exposures
        ],
        [storm["storm_id"]],
        run_id=f"calibration-sensitivity-{storm['storm_id']}",
        catalog_id="calibration-sensitivity",
        sampling_description=(
            "Hand-picked catalog storms used only to compare wind-calibration cohorts' "
            "effect on modeled payouts; not a probabilistic sample."
        ),
        curve_set=curve_set,
    )

    avoided_payout_usd_by_upgrade: dict[str, dict[str, float]] = {}
    for row in result["rows"]:
        avoided_payout_usd_by_upgrade.setdefault(row["upgrade_id"], {})[row["property_id"]] = row["avoided_payout_usd"]

    return {
        "peak_gust_mph": peak_gust_mph,
        "baseline_payout_usd": baseline_payout_usd,
        "portfolio_baseline_payout_usd": round(sum(baseline_payout_usd.values()), 2),
        "avoided_payout_usd_by_upgrade": avoided_payout_usd_by_upgrade,
        "portfolio_avoided_payout_usd_by_upgrade": {
            upgrade_id: round(sum(per_property.values()), 2)
            for upgrade_id, per_property in avoided_payout_usd_by_upgrade.items()
        },
    }


def _diff(value: float, reference: float, *, what: str = "value") -> dict:
    """Absolute and relative difference from `reference`. A zero reference (a property
    with zero baseline payout, say) makes a relative change undefined, not infinite or
    zero, so this reports null with an explicit reason rather than either."""
    absolute = round(value - reference, 2)
    if reference == 0:
        return {"absolute_usd": absolute, "relative": None, "relative_null_reason": f"reference {what} is zero"}
    return {"absolute_usd": absolute, "relative": round((value - reference) / reference, 4)}


def _diff_dict(values: dict[str, float], reference: dict[str, float], *, what: str = "value") -> dict[str, dict]:
    return {key: _diff(values.get(key, 0.0), ref_value, what=what) for key, ref_value in reference.items()}


def _diff_nested(
    values: dict[str, dict[str, float]], reference: dict[str, dict[str, float]], *, what: str = "value"
) -> dict[str, dict[str, dict]]:
    return {
        upgrade_id: _diff_dict(values.get(upgrade_id, {}), ref_by_property, what=what)
        for upgrade_id, ref_by_property in reference.items()
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def build_comparison(
    *,
    tables: dict[float, pd.DataFrame] | None = None,
    pairs: pd.DataFrame | None = None,
    primary_calibration: dict | None = None,
    combined_calibration: dict | None = None,
    current_production_bundle: dict | None = None,
) -> dict:
    """The full comparison. `tables`/`pairs`, if given, are reused rather than
    recomputed (see scripts/calibrate_wind_field.py's all_unit_gust_tables and
    scripts/build_calibration.py). `primary_calibration`/`combined_calibration`, if
    given, are already-built scripts/calibrate_wind_field.py results - an orchestrator
    that builds them anyway (to promote gust_factor_model.json and
    wind_calibration.json) should pass them straight in rather than paying for the
    leave-one-storm-out refit a second time; the default (None) fits each cohort fresh
    with `tables`/`pairs`, so this remains independently callable on its own.
    `current_production_bundle`, if given, is a snapshot captured by the caller before
    any fixture was promoted - see the module docstring; the default (None) omits that
    scenario entirely rather than guessing.
    """
    tables = tables if tables is not None else cwf.all_unit_gust_tables()
    pairs = pairs if pairs is not None else pd.read_csv(fit_gust_factor.PAIRS)

    primary_calibration = (
        primary_calibration if primary_calibration is not None else cwf.calibrate(cc.PRIMARY_COHORT, tables=tables, pairs=pairs)
    )
    combined_calibration = (
        combined_calibration
        if combined_calibration is not None
        else cwf.calibrate(cc.COMBINED_COHORT, tables=tables, pairs=pairs)
    )
    primary_bundle = vwf.bundle_from_calibration(primary_calibration)
    combined_bundle = vwf.bundle_from_calibration(combined_calibration)
    factor_only_bundle = {
        "gust_factor": combined_bundle["gust_factor"],
        "land_exposure_factor": primary_bundle["land_exposure_factor"],
        "outer_decay_exponent": primary_bundle["outer_decay_exponent"],
        "wind_calibration_id": (
            f"diagnostic: {combined_bundle['wind_calibration_id']} gust factor + "
            f"{primary_bundle['wind_calibration_id']} profile"
        ),
    }

    scenarios = {
        "primary": {
            **primary_bundle,
            "role": "production: the primary (2016-2024) cohort's own gust factor and profile fit",
        },
        "factor_only": {
            **factor_only_bundle,
            "role": (
                "diagnostic only: the combined cohort's gust factor with the primary "
                "cohort's decay and land factor frozen, to isolate arithmetic "
                "sensitivity to the gust factor alone. Not an independently fitted "
                "bundle; never a candidate production policy."
            ),
        },
        "combined": {
            **combined_bundle,
            "role": "alternative: the combined (2004-2024) cohort's own gust factor and profile fit, refit jointly",
        },
    }
    if current_production_bundle is not None:
        scenarios["current_production"] = {
            **current_production_bundle,
            "role": (
                "before-change benchmark: the production bundle as captured by the "
                "caller before this comparison's own fits were promoted (see the "
                "module docstring's current_production_bundle note)."
            ),
        }

    effective_multiplier = {
        name: round(bundle["gust_factor"] * bundle["land_exposure_factor"], 4) for name, bundle in scenarios.items()
    }
    wind_accuracy = {name: wind_accuracy_for_bundle(bundle) for name, bundle in scenarios.items()}
    holdout = {
        "primary": primary_calibration["cross_validation"],
        "combined": combined_calibration["cross_validation"],
        "factor_only": None,
        "note": (
            "Republished verbatim from each cohort's own leave-one-storm-out "
            "cross-validation (scripts/calibrate_wind_field.py) - not recomputed here, "
            "and not interchangeable with the fixed-bundle wind_accuracy comparison "
            "above, which scores one already-chosen bundle rather than refitting per "
            "fold. factor_only has no holdout: it is not an independently fitted "
            "bundle, so there is nothing to leave a storm out of."
        ),
    }
    if "current_production" in scenarios:
        holdout["current_production"] = None

    catalog = wind.load_catalog()
    storms = {storm_id: wind.storm_by_id(storm_id, catalog) for storm_id in COMPARISON_STORM_IDS}
    missing = [storm_id for storm_id, storm in storms.items() if storm is None]
    if missing:
        raise cc.DataValidationError(f"comparison storms not in the catalog: {missing}")

    portfolio = _portfolio()
    properties, policies, coordinates = _engine_inputs(portfolio)
    curve_set = claims.load_curve_set()
    eligible_upgrades_by_property = {
        prop.property_id: claims.eligible_upgrades(curve_set["curves"], prop.vulnerability_class)
        for prop in properties
    }

    storms_report = {}
    for storm_id, storm in storms.items():
        by_scenario = {
            name: price_storm(storm, bundle, properties, policies, coordinates, curve_set)
            for name, bundle in scenarios.items()
        }
        primary_result = by_scenario["primary"]
        differences = {
            name: {
                "peak_gust_mph": _diff_dict(result["peak_gust_mph"], primary_result["peak_gust_mph"], what="peak gust"),
                "baseline_payout_usd": _diff_dict(
                    result["baseline_payout_usd"], primary_result["baseline_payout_usd"], what="baseline payout"
                ),
                "portfolio_baseline_payout_usd": _diff(
                    result["portfolio_baseline_payout_usd"],
                    primary_result["portfolio_baseline_payout_usd"],
                    what="portfolio baseline payout",
                ),
                "avoided_payout_usd_by_upgrade": _diff_nested(
                    result["avoided_payout_usd_by_upgrade"],
                    primary_result["avoided_payout_usd_by_upgrade"],
                    what="avoided payout",
                ),
                "portfolio_avoided_payout_usd_by_upgrade": _diff_dict(
                    result["portfolio_avoided_payout_usd_by_upgrade"],
                    primary_result["portfolio_avoided_payout_usd_by_upgrade"],
                    what="portfolio avoided payout",
                ),
            }
            for name, result in by_scenario.items()
            if name != "primary"
        }
        storms_report[storm_id] = {"scenarios": by_scenario, "differences_vs_primary": differences}

    return {
        "calibration_sensitivity_id": CALIBRATION_SENSITIVITY_ID,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "backend/scripts/compare_calibration_cohorts.py",
        "cohort_policy_note": (
            "The primary (2016-2024) cohort is the predetermined production policy. "
            "This report measures whether the combined-cohort or factor-only "
            "alternatives score better or worse; it does not select a policy from "
            "whichever alternative happens to score best on the same held-out "
            "observations, and a material deterioration here is a finding to review, "
            "not a reason to change the assertion in this note."
        ),
        "scenarios": scenarios,
        "effective_multiplier": effective_multiplier,
        "wind_accuracy": wind_accuracy,
        "holdout": holdout,
        "payout_comparison": {
            "config_held_constant": {
                "curve_set_id": curve_set["curve_set_id"],
                "policy_template": claims.load_policy_template(),
                "property_ids": [p.property_id for p in properties],
                "roof_shape_by_property": {p.property_id: p.roof_shape for p in properties},
                "eligible_upgrades_by_property": eligible_upgrades_by_property,
                "storm_ids": list(COMPARISON_STORM_IDS),
                "note": (
                    "The demo's seeded roof-shape assignment "
                    "(scripts/assign_demo_roof_shapes.py), this curve_set_id, the "
                    "policy template and eligible upgrades are held identical across "
                    "every scenario above, so a payout difference between scenarios is "
                    "attributable to the wind bundle alone."
                ),
            },
            "aggregation_note": (
                "Every *_usd figure is a SUM over properties, for one storm and one "
                "scenario, never an average and never summed across storms into an "
                "endpoint total: portfolio_baseline_payout_usd sums each property's "
                "baseline (no-upgrade) payout; each entry of "
                "portfolio_avoided_payout_usd_by_upgrade sums avoided_payout_usd only "
                "over the properties eligible for that upgrade. Per-property figures "
                "(peak_gust_mph, baseline_payout_usd, avoided_payout_usd_by_upgrade) "
                "are reported alongside these sums, never blended with them."
            ),
            "storms": storms_report,
        },
        "hashes": {
            "example_portfolio_sha256": cc.sha256_of_file(PORTFOLIO_PATH),
            "damage_curves_sha256": cc.sha256_of_file(FIXTURES_DIR / "damage_curves.json"),
            "gust_pairs_sha256": cc.sha256_of_file(fit_gust_factor.PAIRS),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture", default=str(FIXTURE))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument(
        "--no-current-production",
        action="store_true",
        help="omit the archived (pre-change) production bundle benchmark",
    )
    args = parser.parse_args(argv)

    current_production_bundle = None if args.no_current_production else vwf.production_bundle()
    report = build_comparison(current_production_bundle=current_production_bundle)

    print("scenarios:")
    for name, bundle in report["scenarios"].items():
        print(
            f"  {name:18s} gust {bundle['gust_factor']}, decay {bundle['outer_decay_exponent']}, "
            f"land {bundle['land_exposure_factor']} (x{report['effective_multiplier'][name]}) - {bundle['role']}"
        )

    print("\nwind accuracy (median modeled/observed ratio, MAE kt):")
    for name, by_cohort in report["wind_accuracy"].items():
        parts = ", ".join(
            f"{cohort}: ratio {metrics.get('median_ratio_modeled_over_observed')}, MAE {metrics.get('mean_absolute_error_kt')}"
            for cohort, metrics in by_cohort.items()
        )
        print(f"  {name:18s} {parts}")

    for storm_id, entry in report["payout_comparison"]["storms"].items():
        print(f"\n{storm_id}:")
        for name, result in entry["scenarios"].items():
            print(f"  {name:18s} portfolio baseline payout ${result['portfolio_baseline_payout_usd']:,.2f}")
        for name, diff in entry["differences_vs_primary"].items():
            d = diff["portfolio_baseline_payout_usd"]
            rel = f"{d['relative']:+.2%}" if d["relative"] is not None else f"null ({d.get('relative_null_reason')})"
            print(f"    {name} vs primary: {d['absolute_usd']:+,.2f} usd ({rel})")

    if not args.no_write:
        Path(args.fixture).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.fixture}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
