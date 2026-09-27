"""Sample-insurer economics: premiums, physical losses and program cash flows, brought
together per event, per program arm, and (only when asked) per illustrative year.

Sections 8 and 9 of the sample-insurer specification (27 September 2026). The central
result is avoided insurer payouts minus premium discounts, grants and administration,
shown beside the homeowner's side so a payout reduction caused by a higher deductible
is visible as a transfer of loss, not a saving.

What this module does NOT do is attach a probability to anything on its own. The
catalog storms are alternative events; their results are reported independently and
never added. An annual figure exists only under the `one_event_or_none` model, whose
event probability and storm weights are the caller's explicit, editable assumption,
and every annual output carries that assumption back with it.

Pure like its neighbours: fixtures and plain dicts in, dicts out, no HTTP. It calls
wind.py, claims.py, premium.py and mitigation_states.py directly, never an endpoint.

Three program arms are compared on the same selected projects:

  - current_book:      nothing done. Current features, current credits, no funding.
  - homeowner_funded:  selected projects completed and credited; the homeowner pays
                       the full quote; the insurer pays only its administration.
  - insurer_cofunded:  the same completed projects and credits; the insurer grants a
                       share of each cost, the homeowner pays the balance.

The last two have identical physical and premium results by construction. Only the
cash changes hands differently, and a test holds the module to that.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from typing import Iterable

from . import claims, mitigation_states as ms, premium, wind

SCHEMA_VERSION = "insurer-demo-v1"

CURRENT_BOOK = "current_book"
HOMEOWNER_FUNDED = "homeowner_funded"
INSURER_COFUNDED = "insurer_cofunded"
ARMS = (CURRENT_BOOK, HOMEOWNER_FUNDED, INSURER_COFUNDED)

EVENT_ONLY = "event_only"
ONE_EVENT_OR_NONE = "one_event_or_none"

# The optimizer enumerates every subset of the available proposals. Ten policies is
# 1,024 subsets; this bound keeps a bigger book from turning a demo request into a
# minute of CPU rather than saying so.
MAX_OPTIMIZED_PROPOSALS = 16

NO_ANNUAL_REASON = (
    "annual_model is event_only: the catalog storms are alternative events, not a "
    "frequency sample, so no annual expectation, NPV or break-even is computed. Enable "
    "the one_event_or_none model with an explicit event probability and storm weights "
    "to see illustrative annual figures."
)


class InsurerError(ValueError):
    """An input this service refuses; the message names the field or id."""


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


def _money(value: float | None) -> float | None:
    return premium.money(value)


def default_program() -> dict:
    return dict(premium.load_insurer_demo()["program"])


def validate_annual_model(model: dict, storm_ids: list[str]) -> dict:
    """The tagged annual model, checked against the storms actually being run."""
    kind = model.get("kind")
    if kind == EVENT_ONLY:
        return {"kind": EVENT_ONLY}
    if kind != ONE_EVENT_OR_NONE:
        raise InsurerError(f"annual_model.kind must be {EVENT_ONLY!r} or {ONE_EVENT_OR_NONE!r}, got {kind!r}")
    probability = model.get("annual_event_probability")
    if not isinstance(probability, (int, float)) or isinstance(probability, bool) or not 0.0 <= probability <= 1.0 or probability != probability:
        raise InsurerError(f"annual_model.annual_event_probability must be a number in [0, 1], got {probability!r}")
    weights = model.get("conditional_storm_weights")
    if not isinstance(weights, dict):
        raise InsurerError("annual_model.conditional_storm_weights must map each run storm id to a weight")
    if set(weights) != set(storm_ids):
        raise InsurerError(
            f"annual_model.conditional_storm_weights must name exactly the storms being run: "
            f"weights for {sorted(weights)}, running {sorted(storm_ids)}. Changing the scenario "
            "set requires an updated probability configuration."
        )
    total = 0.0
    for storm_id, weight in weights.items():
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight != weight or weight in (float("inf"), float("-inf")) or weight < 0:
            raise InsurerError(f"annual_model.conditional_storm_weights[{storm_id}] must be a finite, nonnegative number, got {weight!r}")
        total += weight
    if total <= 0:
        raise InsurerError("annual_model.conditional_storm_weights must sum to a positive number")
    return {
        "kind": ONE_EVENT_OR_NONE,
        "annual_event_probability": float(probability),
        "conditional_storm_weights": {s: float(w) for s, w in weights.items()},
        "storm_probabilities": {s: float(probability) * float(w) / total for s, w in weights.items()},
        "no_event_probability": 1.0 - float(probability),
        "evidence_status": "invented_demo_assumption",
    }


def _policies_for(policy_ids: Iterable[str] | None) -> list[dict]:
    book = premium.load_policies()["policies"]
    if policy_ids is None:
        return list(book)
    wanted = list(policy_ids)
    if len(set(wanted)) != len(wanted):
        raise InsurerError("policy_ids repeats an id")
    by_id = {p["policy_id"]: p for p in book}
    unknown = [pid for pid in wanted if pid not in by_id]
    if unknown:
        raise InsurerError(f"unknown policy ids: {unknown}")
    if not wanted:
        raise InsurerError("policy_ids is empty")
    return [by_id[pid] for pid in wanted]


def _storms_for(storm_ids: Iterable[str], catalog: dict | None) -> tuple[dict, list[dict]]:
    catalog = catalog or wind.load_catalog()
    ids = list(storm_ids)
    if not ids:
        raise InsurerError("storm_ids is empty")
    if len(set(ids)) != len(ids):
        raise InsurerError("storm_ids repeats an id")
    storms = []
    for storm_id in ids:
        storm = wind.storm_by_id(storm_id, catalog)
        if storm is None:
            raise InsurerError(f"unknown storm id {storm_id!r} for catalog {catalog['catalog_id']}; available: {catalog['storm_ids']}")
        storms.append(storm)
    return catalog, storms


def _policy_with_deductible(record: dict, fraction: float | None) -> claims.Policy:
    if fraction is None:
        return ms.policy_from_record(record)
    if not 0.0 <= fraction <= 1.0:
        raise InsurerError(f"deductible_fraction must be in [0, 1], got {fraction}")
    return ms.policy_from_record({**record, "deductible": {"kind": "percent", "fraction": fraction}})


# --------------------------------------------------------------------------- #
# Time value and the annual arithmetic
# --------------------------------------------------------------------------- #


def annual_economics(
    *,
    expected_annual_avoided_payout_usd: float,
    annual_premium_foregone_usd: float,
    annual_admin_usd: float,
    insurer_upfront_usd: float,
    horizon_years: int,
    discount_rate: float,
) -> dict:
    """The specification's annual arithmetic for one program.

    A = expected avoided payout - premium foregone - admin, each year for H years,
    discounted at r; the upfront spend is at time zero. Negative results stay negative.
    """
    factor = premium.pv_factor(horizon_years, discount_rate)
    net_annual = expected_annual_avoided_payout_usd - annual_premium_foregone_usd - annual_admin_usd
    npv = -insurer_upfront_usd + net_annual * factor
    break_even = annual_premium_foregone_usd + annual_admin_usd + insurer_upfront_usd / factor
    return {
        "pv_factor": factor,
        "expected_annual_avoided_payout_usd": _money(expected_annual_avoided_payout_usd),
        "annual_premium_foregone_usd": _money(annual_premium_foregone_usd),
        "annual_admin_usd": _money(annual_admin_usd),
        "insurer_upfront_usd": _money(insurer_upfront_usd),
        "net_annual_insurer_benefit_usd": _money(net_annual),
        "insurer_npv_usd": _money(npv),
        "insurer_roi": round(npv / insurer_upfront_usd, 4) if insurer_upfront_usd > 0 else None,
        "insurer_roi_definition": "insurer NPV / insurer upfront spending; null when the insurer spends nothing upfront",
        "break_even_annual_avoided_payout_usd": _money(break_even),
        "_exact": {"npv": npv, "net_annual": net_annual, "break_even": break_even},
    }


# --------------------------------------------------------------------------- #
# The comparison
# --------------------------------------------------------------------------- #


def compare(
    *,
    preset_id: str | None = None,
    policy_ids: Iterable[str] | None = None,
    storm_ids: Iterable[str] | None = None,
    selected_proposal_ids: Iterable[str] | None = None,
    program: dict | None = None,
    annual_model: dict | None = None,
    deductible_fraction: float | None = None,
    catalog: dict | None = None,
) -> dict:
    """Current book versus the selected projects, homeowner-funded and co-funded, on
    each requested storm, with annual economics only under an explicit annual model.

    `selected_proposal_ids` of None selects every proposal on the chosen policies.
    `deductible_fraction` reprices every policy at that fraction of Coverage A with
    the premium held fixed: the sensitivity axis of section 8, labelled as such.
    """
    demo = premium.load_insurer_demo()
    plan = premium.load_credit_plan()
    preset_id = preset_id or demo["default_preset_id"]
    if preset_id not in demo["presets"]:
        raise InsurerError(f"unknown preset {preset_id!r}; presets: {sorted(demo['presets'])}")
    program = dict(program or demo["program"])
    premium.validate_program(program)
    policies = _policies_for(policy_ids)
    catalog, storms = _storms_for(storm_ids or catalog and catalog["storm_ids"] or wind.load_catalog()["storm_ids"], catalog)
    run_storm_ids = [s["storm_id"] for s in storms]
    annual = validate_annual_model(annual_model or {"kind": EVENT_ONLY}, run_storm_ids)

    known_proposals = {p["proposal"]["proposal_id"]: p["policy_id"] for p in policies if p.get("proposal")}
    if selected_proposal_ids is None:
        selected = set(known_proposals)
    else:
        selected = set(selected_proposal_ids)
        unknown = sorted(selected - set(known_proposals))
        if unknown:
            raise InsurerError(f"unknown or out-of-scope proposal ids: {unknown}")

    # Premiums and funding for each arm. Physical state does not depend on funding, so
    # the two program arms share one premium/feature result and differ in grants only.
    homeowner_program = {**program, "grant_share": 0.0, "grant_cap_usd": 0.0}
    premiums = {
        CURRENT_BOOK: premium.price_program(policies, preset_id, plan, program, selected_proposal_ids=[]),
        HOMEOWNER_FUNDED: premium.price_program(policies, preset_id, plan, homeowner_program, selected_proposal_ids=sorted(selected)),
        INSURER_COFUNDED: premium.price_program(policies, preset_id, plan, program, selected_proposal_ids=sorted(selected)),
    }

    # Physical states: the home as it is, and as the selected project leaves it. One
    # exposure per storm and property, shared by every arm.
    states = [
        ms.state_from_record(record, preset_id, selected=record.get("proposal", {}).get("proposal_id") in selected)
        for record in policies
    ]
    conflicts = [
        {"policy_id": p["policy_id"], "conflict": p["states"][preset_id]["physical_state_conflict"]}
        for p in policies
        if p["states"][preset_id].get("physical_state_conflict")
    ]
    if conflicts:
        raise InsurerError(
            f"preset {preset_id!r} has physical-state conflicts and can only be compared on premiums: "
            + "; ".join(f"{c['policy_id']}: {c['conflict']}" for c in conflicts)
        )
    engine_policies = [_policy_with_deductible(record, deductible_fraction) for record in policies]
    coordinates = [(p["property_id"], p["property"]["latitude"], p["property"]["longitude"]) for p in policies]
    exposures: list[claims.WindExposure] = []
    exposure_detail: list[dict] = []
    track_warnings: list[str] = []
    for storm in storms:
        try:
            storm_exposures, detail = wind.exposures_for_storm(storm, coordinates)
        except ValueError as error:
            raise InsurerError(str(error)) from error
        exposures.extend(storm_exposures)
        exposure_detail.extend(detail)
        track_warnings.extend(wind.track_gaps(storm))
    transitions = ms.compute_transitions(states, engine_policies, exposures, run_storm_ids)

    policy_of_property = {p["property_id"]: p["policy_id"] for p in policies}
    loss_rows = [{"policy_id": policy_of_property[r["property_id"]], **r} for r in transitions["rows"]]

    # Per event: the current book against the program (either arm: same physics).
    events = {}
    for storm_id in run_storm_ids:
        t = transitions["totals_by_storm"][storm_id]
        events[storm_id] = {
            "storm_id": storm_id,
            "current_book": {
                "damage_usd": t["current_damage_usd"],
                "payout_usd": t["current_payout_usd"],
                "uninsured_damage_usd": t["current_uninsured_damage_usd"],
            },
            "program": {
                "damage_usd": t["result_damage_usd"],
                "payout_usd": t["result_payout_usd"],
                "uninsured_damage_usd": t["result_uninsured_damage_usd"],
            },
            "avoided_damage_usd": t["avoided_damage_usd"],
            "avoided_payout_usd": t["avoided_payout_usd"],
            "avoided_uninsured_damage_usd": t["avoided_uninsured_damage_usd"],
            "policies_with_payout_current": sum(1 for r in loss_rows if r["storm_id"] == storm_id and r["current_payout_usd"] > 0),
            "policies_with_payout_program": sum(1 for r in loss_rows if r["storm_id"] == storm_id and r["result_payout_usd"] > 0),
            # "If this storm occurs in the first policy year": one event's avoided
            # payout against one year of concession and the whole upfront spend.
            # Never added across storms, which are alternatives.
            "first_year_insurer_benefit_if_this_storm_usd": {},
        }

    programs = {}
    for arm in ARMS:
        pr = premiums[arm]
        totals = pr["totals"]
        upfront = totals["insurer_upfront_usd"] if arm != CURRENT_BOOK else 0.0
        programs[arm] = {
            "program_id": arm,
            "label": {
                CURRENT_BOOK: "Current book: nothing done",
                HOMEOWNER_FUNDED: "Homeowner-funded: selected projects completed, homeowner pays the full quote",
                INSURER_COFUNDED: "Insurer co-funded: the same projects, insurer grants a share of each cost",
            }[arm],
            "selected_proposal_ids": pr["selected_proposal_ids"],
            "project_count": pr["project_count"],
            "complete": pr["complete"],
            "unavailable": pr["unavailable"],
            "premium": {k: totals[k] for k in ("current_wind_premium_usd", "result_wind_premium_usd", "annual_premium_foregone_usd", "annual_nonwind_premium_usd")},
            "costs": {
                "project_cost_usd": totals["project_cost_usd"] if arm != CURRENT_BOOK else 0.0,
                "insurer_grants_usd": totals["insurer_grants_usd"] if arm != CURRENT_BOOK else 0.0,
                "inspections_usd": totals["inspections_usd"] if arm != CURRENT_BOOK else 0.0,
                "fixed_setup_usd": totals["fixed_setup_usd"] if arm != CURRENT_BOOK else 0.0,
                "annual_admin_usd": totals["annual_admin_usd"] if arm != CURRENT_BOOK else 0.0,
                "insurer_upfront_usd": upfront,
                "homeowner_upfront_usd": totals["homeowner_upfront_usd"] if arm != CURRENT_BOOK else 0.0,
            },
            "homeowner": {
                "annual_premium_savings_usd": totals["annual_premium_foregone_usd"],
                "ten_year_undiscounted_premium_net_usd": totals["homeowner_ten_year_undiscounted_premium_net_usd"] if arm != CURRENT_BOOK else 0.0,
            },
            "annual_economics": None,
            "annual_economics_unavailable_reason": NO_ANNUAL_REASON if annual["kind"] == EVENT_ONLY else None,
        }
        for storm_id, event in events.items():
            if arm == CURRENT_BOOK:
                event["first_year_insurer_benefit_if_this_storm_usd"][arm] = 0.0
            elif pr["complete"]:
                event["first_year_insurer_benefit_if_this_storm_usd"][arm] = _money(
                    event["avoided_payout_usd"] - totals["annual_premium_foregone_usd"] - totals["annual_admin_usd"] - upfront
                )
            else:
                event["first_year_insurer_benefit_if_this_storm_usd"][arm] = None

    # Annual economics, only under the explicit model.
    if annual["kind"] == ONE_EVENT_OR_NONE:
        probabilities = annual["storm_probabilities"]
        expected_current = sum(probabilities[s] * events[s]["current_book"]["payout_usd"] for s in run_storm_ids)
        expected_result = sum(probabilities[s] * events[s]["program"]["payout_usd"] for s in run_storm_ids)
        expected_uninsured_current = sum(probabilities[s] * events[s]["current_book"]["uninsured_damage_usd"] for s in run_storm_ids)
        expected_uninsured_result = sum(probabilities[s] * events[s]["program"]["uninsured_damage_usd"] for s in run_storm_ids)
        for arm in ARMS:
            entry = programs[arm]
            if arm == CURRENT_BOOK:
                avoided, foregone, admin, upfront = 0.0, 0.0, 0.0, 0.0
            elif entry["complete"]:
                avoided = expected_current - expected_result
                foregone = entry["premium"]["annual_premium_foregone_usd"]
                admin = entry["costs"]["annual_admin_usd"]
                upfront = entry["costs"]["insurer_upfront_usd"]
            else:
                entry["annual_economics_unavailable_reason"] = "a selected project's cost is unavailable; see unavailable"
                continue
            econ = annual_economics(
                expected_annual_avoided_payout_usd=avoided, annual_premium_foregone_usd=foregone, annual_admin_usd=admin,
                insurer_upfront_usd=upfront, horizon_years=program["horizon_years"], discount_rate=program["discount_rate"],
            )
            exact = econ.pop("_exact")
            mean_conditional_avoided = (expected_current - expected_result) / annual["annual_event_probability"] if annual["annual_event_probability"] > 0 else None
            econ["expected_annual_payout_current_usd"] = _money(expected_current if arm != CURRENT_BOOK else expected_current)
            econ["expected_annual_payout_program_usd"] = _money(expected_result if arm != CURRENT_BOOK else expected_current)
            # The probability at which this program breaks even, holding the storm
            # weights fixed: the one number that turns the invented event probability
            # into a question the reader can judge.
            econ["break_even_annual_event_probability"] = (
                round(exact["break_even"] / mean_conditional_avoided, 4)
                if (arm != CURRENT_BOOK and mean_conditional_avoided and mean_conditional_avoided > 0)
                else None
            )
            econ["homeowner"] = {
                "expected_annual_avoided_uninsured_damage_usd": _money((expected_uninsured_current - expected_uninsured_result) if arm != CURRENT_BOOK else 0.0),
                "premium_only_npv_usd": _money(
                    -entry["costs"]["homeowner_upfront_usd"] + entry["premium"]["annual_premium_foregone_usd"] * econ["pv_factor"]
                ) if arm != CURRENT_BOOK else 0.0,
                "expanded_npv_including_avoided_uninsured_damage_usd": _money(
                    -entry["costs"]["homeowner_upfront_usd"]
                    + (entry["premium"]["annual_premium_foregone_usd"] + (expected_uninsured_current - expected_uninsured_result)) * econ["pv_factor"]
                ) if arm != CURRENT_BOOK else 0.0,
                "note": "Insured claim payments are the insurer's; only uninsured damage avoided is the homeowner's physical loss avoided.",
            }
            econ["assumption"] = {
                "kind": ONE_EVENT_OR_NONE,
                "annual_event_probability": annual["annual_event_probability"],
                "storm_probabilities": {s: round(p, 6) for s, p in probabilities.items()},
                "no_event_probability": annual["no_event_probability"],
                "evidence_status": annual["evidence_status"],
                "note": "At most one event per year; upfront costs at time zero, recurring flows at year end; unchanged renewals, full repair between years, immediate and undiminishing mitigation.",
            }
            entry["annual_economics"] = econ
            entry["annual_economics_unavailable_reason"] = None

    warnings = list(transitions["warnings"]) + track_warnings
    config = {
        "schema_version": SCHEMA_VERSION,
        "preset_id": preset_id,
        "policy_ids": [p["policy_id"] for p in policies],
        "storm_ids": run_storm_ids,
        "selected_proposal_ids": sorted(selected),
        "program": program,
        "annual_model": {k: v for k, v in annual.items() if k not in ("storm_probabilities", "no_event_probability")},
        "deductible_fraction": deductible_fraction,
    }
    provenance = {
        "credit_plan_id": plan["plan_id"],
        "curve_set_id": transitions["curve_set_id"],
        "wind_metric": transitions["wind_metric"],
        "wind_calibration_id": wind.load_wind_calibration()["wind_calibration_id"],
        "storm_catalog_id": catalog["catalog_id"],
        "insurer_fixture_provenance": demo["provenance"],
        "input_config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest(),
    }
    return {
        **config,
        "insurer": demo["insurer"],
        "preset": demo["presets"][preset_id],
        "complete": premiums[INSURER_COFUNDED]["complete"],
        "unavailable": premiums[INSURER_COFUNDED]["unavailable"],
        "counts": {"policies_requested": len(policies), "policies_evaluated": len(policies), "storms_requested": len(run_storm_ids), "storms_evaluated": len(run_storm_ids)},
        "policies": premiums[INSURER_COFUNDED]["policies"],
        "policies_homeowner_funded": premiums[HOMEOWNER_FUNDED]["policies"],
        "curve_selection": transitions["curve_selection"],
        "loss_rows": loss_rows,
        "events": events,
        "programs": programs,
        "deductible_sensitivity_note": (
            f"Every policy repriced at {deductible_fraction:.0%} of Coverage A with the premium held fixed: a "
            "simplifying assumption, not a priced alternative deductible."
            if deductible_fraction is not None else None
        ),
        "wind_exposure_detail": exposure_detail,
        "warnings": warnings,
        "notes": [
            "Storms are alternative events reported independently; nothing here adds results across storms.",
            "Insurer figures are gross payouts before reinsurance, claims expenses, taxes and commissions; not company profit or a combined ratio.",
            "Adoption is an explicit scenario assumption: the selected projects are assumed completed. A grant does not, by itself, change what gets built.",
            *transitions["notes"],
        ],
        "units": {"money": "USD", "wind": transitions["wind_metric"], "credits_rates_probabilities": "fraction of 1"},
        "provenance": provenance,
    }


# --------------------------------------------------------------------------- #
# Budget selection
# --------------------------------------------------------------------------- #


def optimize_subsets(candidates: list[dict], program: dict, *, objective: str = "insurer_npv") -> dict:
    """Choose the subset of candidate projects with the highest insurer NPV within the
    upfront budget, from an exhaustive enumeration.

    Each candidate is per-policy and additive: `proposal_id`, `effective_cost_usd`,
    `insurer_grant_usd`, `annual_premium_foregone_usd`, `expected_annual_avoided_payout_usd`.
    Fixed setup is charged once for any nonempty subset. The empty subset is always a
    candidate; ties go to lower spending, then to the lexicographically smaller sorted
    id list. Pure arithmetic: the caller supplies the per-policy expectations.
    """
    if objective != "insurer_npv":
        raise InsurerError(f"unsupported objective {objective!r}; only 'insurer_npv' is implemented")
    if len(candidates) > MAX_OPTIMIZED_PROPOSALS:
        raise InsurerError(f"{len(candidates)} candidates exceeds the {MAX_OPTIMIZED_PROPOSALS} this exhaustive optimizer enumerates")
    premium.validate_program(program)
    factor = premium.pv_factor(program["horizon_years"], program["discount_rate"])
    budget = program["budget_usd"]
    best = None
    evaluated = 0
    for size in range(len(candidates) + 1):
        for combo in itertools.combinations(candidates, size):
            evaluated += 1
            ids = tuple(sorted(c["proposal_id"] for c in combo))
            grants = sum(c["insurer_grant_usd"] for c in combo)
            upfront = grants + program["inspection_usd_per_project"] * len(combo) + (program["fixed_setup_usd"] if combo else 0.0)
            if upfront > budget + 1e-9:
                continue
            foregone = sum(c["annual_premium_foregone_usd"] for c in combo)
            avoided = sum(c["expected_annual_avoided_payout_usd"] for c in combo)
            admin = program["annual_admin_usd"] if combo else 0.0
            npv = -upfront + (avoided - foregone - admin) * factor
            key = (-round(npv, 6), round(upfront, 6), ids)
            if best is None or key < best[0]:
                best = (key, {"proposal_ids": list(ids), "insurer_npv_usd": npv, "insurer_upfront_usd": upfront, "insurer_grants_usd": grants,
                              "annual_premium_foregone_usd": foregone, "expected_annual_avoided_payout_usd": avoided, "annual_admin_usd": admin})
    assert best is not None  # the empty subset always fits
    chosen = best[1]
    return {
        "objective": objective,
        "subsets_evaluated": evaluated,
        "budget_usd": budget,
        "selected_proposal_ids": chosen["proposal_ids"],
        "insurer_npv_usd": _money(chosen["insurer_npv_usd"]),
        "insurer_upfront_usd": _money(chosen["insurer_upfront_usd"]),
        "budget_remaining_usd": _money(budget - chosen["insurer_upfront_usd"]),
        "insurer_grants_usd": _money(chosen["insurer_grants_usd"]),
        "annual_premium_foregone_usd": _money(chosen["annual_premium_foregone_usd"]),
        "expected_annual_avoided_payout_usd": _money(chosen["expected_annual_avoided_payout_usd"]),
        "verdict": (
            "No funded projects improve the modeled insurer result within the budget."
            if not chosen["proposal_ids"] else f"{len(chosen['proposal_ids'])} of {len(candidates)} candidate projects selected."
        ),
        "tie_break": "highest insurer NPV, then lower upfront spending, then lexicographically sorted proposal ids",
    }


def optimize(**kwargs) -> dict:
    """`compare` under an explicit annual model, then the budget selection over the
    proposals whose cost is known, and the comparison re-run on the chosen subset."""
    annual_model = kwargs.get("annual_model") or {"kind": EVENT_ONLY}
    if annual_model.get("kind") != ONE_EVENT_OR_NONE:
        raise InsurerError("optimization needs the one_event_or_none annual model; event-only mode has no expected value to optimize")
    full = compare(**{**kwargs, "selected_proposal_ids": None})
    program = full["program"]

    # Per-policy expected avoided payout from the all-projects run: losses are
    # independent across policies, so each policy's contribution is its own.
    avoided_by_policy: dict[str, float] = {}
    probabilities = validate_annual_model(annual_model, full["storm_ids"])["storm_probabilities"]
    for row in full["loss_rows"]:
        avoided_by_policy[row["policy_id"]] = avoided_by_policy.get(row["policy_id"], 0.0) + probabilities[row["storm_id"]] * row["avoided_payout_usd"]
    candidates, excluded = [], []
    for row in full["policies"]:
        if not row["selected"] or not row["is_project"]:
            continue
        if row["cost_unavailable_reason"]:
            excluded.append({"proposal_id": row["proposal_id"], "policy_id": row["policy_id"], "reason": row["cost_unavailable_reason"]})
            continue
        candidates.append({
            "proposal_id": row["proposal_id"], "policy_id": row["policy_id"], "effective_cost_usd": row["effective_cost_usd"],
            "insurer_grant_usd": row["insurer_grant_usd"], "annual_premium_foregone_usd": row["annual_premium_foregone_usd"],
            "expected_annual_avoided_payout_usd": avoided_by_policy.get(row["policy_id"], 0.0),
        })
    selection = optimize_subsets(candidates, program)
    chosen = compare(**{**kwargs, "selected_proposal_ids": selection["selected_proposal_ids"]})
    return {
        **chosen,
        "optimization": {
            **selection,
            "candidates": candidates,
            "excluded": excluded,
            "rejected_proposal_ids": sorted(c["proposal_id"] for c in candidates if c["proposal_id"] not in selection["selected_proposal_ids"]),
            "note": "The all-project default is a demonstration case, not this optimizer's presumed recommendation; the empty subset was evaluated and wins when every program is negative.",
        },
    }
