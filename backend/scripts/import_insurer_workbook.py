"""Build the sample-insurer fixtures from the workbook extract, with their provenance.

Development-only. The server never reads Excel: this script turns
`data/insurer_demo/workbook_extract.json` (the hand-checked extract of
`StormShield_Premium_Discount_POC_Revised.xlsx`, see the README beside it) into the three
runtime fixtures the premium engine and the insurer endpoints read:

    backend/app/fixtures/premium_credit_plan.json   credits and zone rates
    backend/app/fixtures/insurer_policies.json      ten policies joined to the demo portfolio
    backend/app/fixtures/insurer_demo.json          insurer, presets, program defaults, totals

Two presets are produced from the same workbook inputs:

  - workbook_reference: the workbook exactly as written. Premium-only; two of its
    post-2002 homes are recorded with no straps, which the app's post-2002 archetype
    already includes, so the conflict is recorded rather than resolved.
  - app_consistent_demo: the default. Class-inherent straps are treated as installed
    AND credited at baseline for post-2002 homes, applied equally to both arms. That
    turns P008's "install straps" into a no-op and changes P005's proposal from
    "shutters" to "both", which is why its premium figures differ from the workbook's.

The join to the demo portfolio is explicit (workbook row id 1 -> P001) and checked field
by field; nothing is joined by row position alone. The extract carries the workbook's
own totals, and this script refuses to write fixtures unless the premium engine lands on
exactly those numbers, for both presets.

    python scripts/import_insurer_workbook.py
    python scripts/import_insurer_workbook.py --workbook /path/to/StormShield_Premium_Discount_POC_Revised.xlsx

With --workbook, the file's SHA-256 is checked against the hash recorded in the extract,
and, if openpyxl is installed, the credit and rate cells are read back and compared.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import premium  # noqa: E402

FIXTURES = BACKEND / "app" / "fixtures"
EXTRACT = BACKEND.parent / "data" / "insurer_demo" / "workbook_extract.json"
PORTFOLIO = FIXTURES / "example_portfolio.json"

WORKBOOK_NAME = "StormShield_Premium_Discount_POC_Revised.xlsx"
CREDIT_CELLS = {"sheet": "Discount Tier Assumptions", "credits": "A5:B8", "rates": "A12:B13"}
POLICY_SHEET = "Synthetic Policies (yours)"
SUMMARY_CELLS = {"sheet": "Portfolio Summary", "range": "B4:B16"}

WORKBOOK_PRESET = "workbook_reference"
APP_PRESET = "app_consistent_demo"

# Features the app's vulnerability classes carry by construction (see
# scripts/build_damage_curves.py: post-2002 code means roof-to-wall straps).
CLASS_INHERENT_FEATURES = {"pre_fbc_2002": (), "post_fbc_2002": ("roof_straps",)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Join and normalize
# --------------------------------------------------------------------------- #


def join_property(entry: dict, portfolio: dict) -> dict:
    """The demo-portfolio record this workbook row is about, checked field by field."""
    expected_id = f"P{entry['frontend_property_id']:03d}"
    if entry["property_id"] != expected_id:
        raise SystemExit(f"{entry['policy_id']}: workbook id {entry['frontend_property_id']} maps to {expected_id}, extract says {entry['property_id']}")
    record = next((p for p in portfolio["properties"] if p["property_id"] == expected_id), None)
    if record is None:
        raise SystemExit(f"{entry['policy_id']}: {expected_id} is not in {PORTFOLIO.name}")
    for field in ("address", "city", "county", "latitude", "longitude", "replacement_cost_usd", "vulnerability_class", "roof_shape"):
        if entry["property"][field] != record[field]:
            raise SystemExit(f"{entry['policy_id']}: {field} differs - extract {entry['property'][field]!r}, portfolio {record[field]!r}. Review the join; do not trust row position.")
    return dict(record)


def states_for(entry: dict, record: dict) -> dict:
    """Both presets' installed/credited feature sets, derived from the WORKBOOK's values."""
    workbook_installed = premium.canonical_features(entry["source"]["original_current_features"])
    inherent = CLASS_INHERENT_FEATURES[record["vulnerability_class"]]
    missing_inherent = tuple(sorted(set(inherent) - set(workbook_installed)))

    workbook_state = {
        "installed_features": list(workbook_installed),
        "credited_features": list(workbook_installed),
        "physical_state_conflict": (
            f"{record['vulnerability_class']} includes {', '.join(missing_inherent)} by construction "
            "but the workbook records none installed. Premium-only preset; use the "
            f"{APP_PRESET} preset for any physical loss comparison."
            if missing_inherent else None
        ),
        "normalization_note": None,
    }
    normalized = tuple(sorted(set(workbook_installed) | set(inherent)))
    app_state = {
        "installed_features": list(normalized),
        "credited_features": list(normalized),
        "physical_state_conflict": None,
        "normalization_note": (
            "Class-inherent straps assumed installed and credited at baseline." if missing_inherent else None
        ),
    }
    return {WORKBOOK_PRESET: workbook_state, APP_PRESET: app_state}


def build_policy(entry: dict, portfolio: dict, insurer_id: str) -> dict:
    record = join_property(entry, portfolio)
    source = entry["source"]
    proposal = {
        "proposal_id": entry["proposal"]["proposal_id"],
        "policy_id": entry["policy_id"],
        "requested_features": list(premium.canonical_features(source["original_proposed_features"])),
        "quoted_scope": list(premium.canonical_features(source["original_proposed_features"])),
        "quote_usd": source["original_quote_usd"],
        "incremental_scope_confirmed": bool(entry["proposal"].get("incremental_scope_confirmed", False)),
        "source": {
            "workbook": WORKBOOK_NAME,
            "sheet": source["sheet"],
            "range": source["range"],
            "original_current_features": list(source["original_current_features"]),
            "original_proposed_features": list(source["original_proposed_features"]),
            "original_quote_usd": source["original_quote_usd"],
        },
    }
    return {
        "policy_id": entry["policy_id"],
        "insurer_id": insurer_id,
        "property_id": record["property_id"],
        "frontend_property_id": entry["frontend_property_id"],
        "property": record,
        "coverage_a_usd": float(entry["coverage_a_usd"]),
        "coverage_limit_usd": float(entry["coverage_limit_usd"]),
        "deductible": dict(entry["deductible"]),
        "rating_value_usd": float(entry["rating_value_usd"]),
        "rating_zone": entry["rating_zone"],
        "annual_nonwind_premium_usd": entry.get("annual_nonwind_premium_usd"),
        "states": states_for(entry, record),
        "proposal": proposal,
    }


# --------------------------------------------------------------------------- #
# Reconciliation against the workbook's own totals
# --------------------------------------------------------------------------- #


def reconcile(policies: list[dict], plan: dict, program: dict, extract: dict) -> dict[str, dict]:
    """Price both presets and insist they reproduce the extract's totals to the cent."""
    checks = {
        WORKBOOK_PRESET: (extract["reference_totals"]["workbook_reference"], (
            "project_cost_usd", "current_wind_premium_usd", "result_wind_premium_usd",
            "annual_premium_foregone_usd", "owner_funded_ten_year_undiscounted_premium_net_usd",
        )),
        APP_PRESET: (extract["reference_totals"]["normalized_all_projects"], (
            "project_cost_usd", "current_wind_premium_usd", "result_wind_premium_usd", "insurer_grants_usd",
            "annual_premium_foregone_usd", "owner_funded_ten_year_undiscounted_premium_net_usd",
            "inspections_usd", "fixed_setup_usd", "insurer_upfront_usd", "homeowner_upfront_usd",
        )),
    }
    results = {}
    for preset_id, (expected, fields) in checks.items():
        result = premium.price_program(policies, preset_id, plan, program)
        if not result["complete"]:
            raise SystemExit(f"{preset_id}: cost unavailable for {result['unavailable']}")
        for field in fields:
            got, want = result["totals"][field], expected[field]
            if abs(got - want) > 0.005:
                raise SystemExit(f"{preset_id}: {field} computed {got} but the workbook says {want}")
        results[preset_id] = result
    # The extract's per-policy reference outputs are the specification's own hand
    # calculation of the normalized preset; they must agree too.
    by_id = {row["policy_id"]: row for row in results[APP_PRESET]["policies"]}
    for entry in extract["policies"]:
        row, ref = by_id[entry["policy_id"]], entry["reference_outputs"]
        for mine, theirs in (
            ("current_wind_premium_usd", "current_wind_premium_usd"), ("result_wind_premium_usd", "result_wind_premium_usd"),
            ("annual_premium_foregone_usd", "annual_premium_foregone_usd"), ("insurer_grant_usd", "insurer_grant_usd"),
            ("homeowner_upfront_usd", "homeowner_upfront_usd"),
        ):
            if abs(row[mine] - ref[theirs]) > 0.005:
                raise SystemExit(f"{entry['policy_id']} {mine}: computed {row[mine]}, extract says {ref[theirs]}")
        if list(row["features_added"]) != list(premium.canonical_features(entry["proposal"]["features_added"])):
            raise SystemExit(f"{entry['policy_id']}: features_added {row['features_added']} vs extract {entry['proposal']['features_added']}")
    return results


# --------------------------------------------------------------------------- #
# Optional: the workbook itself
# --------------------------------------------------------------------------- #


def verify_workbook(path: Path, extract: dict) -> dict:
    recorded = extract["source_hashes_sha256"][WORKBOOK_NAME]
    actual = sha256(path)
    if actual != recorded:
        raise SystemExit(f"{path.name}: sha256 {actual} does not match the extract's {recorded}. Re-extract before importing.")
    checked = {"sha256_verified": True, "cells_verified": False}
    try:
        import openpyxl  # type: ignore
    except ImportError:
        print("openpyxl not installed: hash verified, cells not read back", file=sys.stderr)
        return checked
    book = openpyxl.load_workbook(path, data_only=True)
    sheet = book[CREDIT_CELLS["sheet"]]
    credits = {str(row[0].value).strip().lower().replace(" ", "_").replace("+", "_"): row[1].value for row in sheet[CREDIT_CELLS["credits"]]}
    rates = {str(row[0].value).strip(): row[1].value for row in sheet[CREDIT_CELLS["rates"]]}
    for key, value in extract["credit_plan"].items():
        if key not in credits or abs(float(credits[key]) - value) > 1e-9:
            raise SystemExit(f"credit {key}: workbook {credits.get(key)!r} vs extract {value}")
    if sorted(float(v) for v in rates.values()) != sorted(extract["zone_rates"].values()):
        raise SystemExit(f"zone rates: workbook {rates} vs extract {extract['zone_rates']}")
    checked["cells_verified"] = True
    return checked


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extract", type=Path, default=EXTRACT)
    parser.add_argument("--workbook", type=Path, default=None, help="local copy of the workbook, to verify against the extract")
    args = parser.parse_args()

    extract = load(args.extract)
    portfolio = load(PORTFOLIO)
    portfolio_hash = sha256(PORTFOLIO)
    recorded_portfolio_hash = extract["source_hashes_sha256"].get("example_portfolio.json")
    if recorded_portfolio_hash != portfolio_hash:
        raise SystemExit(
            f"{PORTFOLIO.name} sha256 {portfolio_hash} differs from the {recorded_portfolio_hash} the extract was "
            "made against. Review the join before importing; a changed portfolio is not joined by row position."
        )
    workbook_check = verify_workbook(args.workbook, extract) if args.workbook else {"sha256_verified": False, "cells_verified": False}

    insurer = dict(extract["insurer"])
    plan = {
        "plan_id": "workbook-discount-tiers-v1",
        "schema_version": premium.SCHEMA_VERSION,
        "evidence_status": "assumed",
        "features": list(premium.FEATURES),
        "credit_plan": dict(extract["credit_plan"]),
        "zone_rates": dict(extract["zone_rates"]),
        "credit_basis": "Fraction of the uncredited wind premium, looked up for the union of credited features. Both features earn the combined tier, not the sum of the single tiers.",
        "rate_basis": "Annual wind premium as a fraction of the policy's rating value (the workbook's home value), by rating zone.",
        "source": {"workbook": WORKBOOK_NAME, "sha256": extract["source_hashes_sha256"][WORKBOOK_NAME], **CREDIT_CELLS},
    }
    premium.validate_credit_plan(plan)
    policies = [build_policy(entry, portfolio, insurer["insurer_id"]) for entry in extract["policies"]]
    for policy in policies:
        premium.validate_policy(policy)
    program = dict(extract["program"])
    premium.validate_program(program)
    results = reconcile(policies, plan, program, extract)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    provenance = {
        "generated_at": generated_at,
        "script": "backend/scripts/import_insurer_workbook.py",
        "extract": str(args.extract.relative_to(BACKEND.parent)) if args.extract.is_relative_to(BACKEND.parent) else str(args.extract),
        "workbook": {"name": WORKBOOK_NAME, "sha256": extract["source_hashes_sha256"][WORKBOOK_NAME], **workbook_check},
        "example_portfolio_sha256": portfolio_hash,
        "app_snapshot_sha256": extract["source_hashes_sha256"].get("weather-risk-platform-main (2).zip"),
        "cells": {**CREDIT_CELLS, "policies": f"{POLICY_SHEET}!A5:AA14", "summary": f"{SUMMARY_CELLS['sheet']}!{SUMMARY_CELLS['range']}"},
    }

    dump(FIXTURES / "premium_credit_plan.json", plan)
    dump(FIXTURES / "insurer_policies.json", {
        "schema_version": premium.SCHEMA_VERSION,
        "insurer_id": insurer["insurer_id"],
        "portfolio_id": insurer["portfolio_id"],
        "evidence_status": "assumed",
        "note": (
            "Ten illustrative policies on the demo portfolio. Coverage A and the limit equal the "
            "property's replacement cost (a demo assumption); the deductible is the existing 5% "
            "template; the rating value and zone are the workbook's; class and roof shape are the "
            "portfolio's assigned placeholders. installed_features and credited_features are kept "
            "separate per preset even where they coincide, so a credit-only change can be modeled."
        ),
        "frontend_property_map": {str(p["frontend_property_id"]): p["property_id"] for p in policies},
        "policies": policies,
        "provenance": provenance,
    })
    dump(FIXTURES / "insurer_demo.json", {
        "schema_version": premium.SCHEMA_VERSION,
        "status": "fixtures_and_premium_engine_only",
        "insurer": insurer,
        "default_preset_id": APP_PRESET,
        "presets": {
            WORKBOOK_PRESET: {
                "label": "Workbook reference",
                "purpose": "Unchanged workbook inputs, premium-only reconciliation. Physical-state conflicts are reported, not resolved.",
                "physical_state_conflicts": {p["policy_id"]: p["states"][WORKBOOK_PRESET]["physical_state_conflict"] for p in policies if p["states"][WORKBOOK_PRESET]["physical_state_conflict"]},
            },
            APP_PRESET: {
                "label": "App-consistent demo",
                "purpose": "Default insurer simulation. Post-2002 homes have their class-inherent roof straps installed and credited at baseline, in both arms; this is a demo assumption, not property evidence.",
                "normalized_policies": {p["policy_id"]: p["states"][APP_PRESET]["normalization_note"] for p in policies if p["states"][APP_PRESET]["normalization_note"]},
            },
        },
        "credit_plan_id": plan["plan_id"],
        "policy_plan_id": "demo-deductible-5pct",
        "deductible_sensitivity_fractions": [0.02, 0.05, 0.10],
        "program": program,
        "program_note": "Grant share, cap, inspection, setup, admin, budget, horizon and discount rate are new demo assumptions from the specification, not workbook inputs.",
        "annual_model": dict(extract["annual_model"]),
        "optional_annual_preset": dict(extract["optional_annual_preset"]),
        "reference_totals": {
            "workbook_reference": dict(extract["reference_totals"]["workbook_reference"]),
            "app_consistent_demo": dict(extract["reference_totals"]["normalized_all_projects"]),
            "note": "The workbook's own totals (Portfolio Summary) and the specification's normalized totals. The importer refuses to write these fixtures unless the premium engine reproduces both to the cent; see backend/tests/test_premium.py.",
        },
        "computed_totals": {preset_id: result["totals"] for preset_id, result in results.items()},
        "loss_results": None,
        "loss_results_note": "Computed on request by app/mitigation_states.py (one current/resulting pair per policy and event, on the curve set's physical states) rather than stored here; the insurer endpoints that call it come next.",
        "provenance": provenance,
    })
    print(f"wrote premium_credit_plan.json, insurer_policies.json ({len(policies)} policies), insurer_demo.json")
    for preset_id, result in results.items():
        t = result["totals"]
        print(f"  {preset_id}: premium {t['current_wind_premium_usd']:,.2f} -> {t['result_wind_premium_usd']:,.2f}, "
              f"foregone {t['annual_premium_foregone_usd']:,.2f}/yr, projects {result['project_count']}, insurer upfront {t['insurer_upfront_usd']:,.2f}")


if __name__ == "__main__":
    main()
