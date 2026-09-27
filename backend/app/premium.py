"""Premium engine for the sample insurer: mitigation features -> credit -> wind premium,
plus the project quotes, grants and homeowner premium-only economics that hang off it.

Scope is section 7 of the sample-insurer specification (27 September 2026) and the
homeowner premium-only outputs of its section 8. Nothing here touches wind, damage
curves or claims: a premium is a function of the rating value, the zone rate and the
credit for the feature set the insurer has *credited*, which is deliberately kept apart
from the feature set physically *installed*. A credit-only change must show up as a
premium change and nothing else, which is only possible if the two sets are separate.

Like `claims.py`, this module imports no FastAPI and does no HTTP. It takes plain dicts
in the shape of the fixtures (`fixtures/insurer_policies.json`,
`fixtures/premium_credit_plan.json`, `fixtures/insurer_demo.json`) and returns dicts, so
the same functions serve the importer that builds those fixtures, the tests that pin the
workbook reconciliation, and the endpoints to come.

Three rules worth stating once:

  - Credits are looked up for the *union* of features, never added. Both features earn
    the plan's combined tier (25% in the workbook), not 8% + 12%.
  - A quote pays for new features only. A proposal that adds nothing costs nothing and
    is not a project; a package quote that overlaps features already installed is
    unusable until someone confirms an incremental price. Unknown is null with a
    reason, never zero.
  - Full precision inside, rounding once on the way out, half away from zero, which is
    what the front end's currency formatter does too.
"""

from __future__ import annotations

import json
import math
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Iterable, NamedTuple

FIXTURES = Path(__file__).resolve().parent / "fixtures"

SCHEMA_VERSION = "insurer-demo-v1"

# The mitigation features an insurer can credit, in canonical (sorted) order.
FEATURES = ("roof_straps", "shutters")

# Credit-plan key for each feature set. The workbook names the combined tier
# "shutters_roof_straps", which is not the sorted order, so the mapping is explicit.
CREDIT_KEYS: dict[tuple[str, ...], str] = {
    (): "none",
    ("roof_straps",): "roof_straps",
    ("shutters",): "shutters",
    ("roof_straps", "shutters"): "shutters_roof_straps",
}

# Reasons an effective project cost can be unavailable. Published verbatim so a client
# can explain a null instead of showing a dash.
QUOTE_MISSING = "quote_missing"
QUOTE_SCOPE_UNCONFIRMED = "quote_scope_unconfirmed"
QUOTE_SCOPE_INCOMPLETE = "quote_scope_incomplete"

# The workbook's owner-funded convention: ten years of premium savings, undiscounted,
# less the whole project cost. Fixed at ten on purpose; the program's horizon drives the
# discounted figures, not this reconciliation number.
WORKBOOK_UNDISCOUNTED_YEARS = 10


class PremiumError(ValueError):
    """Any input this engine refuses. The message names the field and the record."""


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #


def money(value: float | None) -> float | None:
    """Round to cents, half away from zero, once, on the way out. None stays None."""
    if value is None:
        return None
    rounded = float(Decimal(repr(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return 0.0 if rounded == 0 else rounded  # never -0.0


def _finite(name: str, value: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PremiumError(f"{name} must be a finite number, got {value!r}")
    if minimum is not None and value < minimum:
        raise PremiumError(f"{name} must be at least {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise PremiumError(f"{name} must be at most {maximum}, got {value}")
    return float(value)


# --------------------------------------------------------------------------- #
# Features and credits
# --------------------------------------------------------------------------- #


def canonical_features(features: Iterable[str] | None) -> tuple[str, ...]:
    """Deduplicated, sorted, validated. The only shape a feature set takes in here."""
    result = set()
    for feature in features or ():
        if feature not in FEATURES:
            raise PremiumError(f"unknown mitigation feature {feature!r}; expected one of {FEATURES}")
        result.add(feature)
    return tuple(sorted(result))


def credit_key(features: Iterable[str] | None) -> str:
    return CREDIT_KEYS[canonical_features(features)]


def credit_for(features: Iterable[str] | None, plan: dict) -> float:
    """The plan's credit for this feature set as a fraction in [0, 1]. Looked up, never summed."""
    key = credit_key(features)
    credits = plan["credit_plan"]
    if key not in credits:
        raise PremiumError(f"credit plan {plan.get('plan_id')!r} has no tier {key!r}")
    return _finite(f"credit_plan[{key}]", credits[key], minimum=0.0, maximum=1.0)


def zone_rate(zone: str, plan: dict) -> float:
    rates = plan["zone_rates"]
    if zone not in rates:
        raise PremiumError(f"unknown rating zone {zone!r}; plan has {sorted(rates)}")
    return _finite(f"zone_rates[{zone}]", rates[zone], minimum=0.0, maximum=1.0)


def uncredited_wind_premium(rating_value_usd: float, rate: float) -> float:
    return _finite("rating_value_usd", rating_value_usd, minimum=0.0) * rate


def wind_premium(rating_value_usd: float, rate: float, credit: float) -> float:
    """rating value x zone rate x (1 - credit). The credit never touches Coverage A."""
    return uncredited_wind_premium(rating_value_usd, rate) * (1.0 - credit)


# --------------------------------------------------------------------------- #
# Physical state transitions and quotes
# --------------------------------------------------------------------------- #


class Transition(NamedTuple):
    features_added: tuple[str, ...]
    resulting_features: tuple[str, ...]


def resolve_transition(installed: Iterable[str] | None, requested: Iterable[str] | None) -> Transition:
    """features_added = requested - installed; resulting = installed | requested."""
    have = set(canonical_features(installed))
    want = set(canonical_features(requested))
    return Transition(tuple(sorted(want - have)), tuple(sorted(have | want)))


def effective_cost(
    quote_usd: float | None,
    quoted_scope: Iterable[str] | None,
    features_added: Iterable[str],
    incremental_scope_confirmed: bool,
) -> tuple[float | None, str | None]:
    """What the project actually costs, or None with the reason it cannot be known.

    A quote is usable when it prices exactly the features being added. A package quote
    that also covers features the home already has is usable only once someone has
    confirmed it as the incremental price; dividing a package price is never done here.
    """
    added = canonical_features(features_added)
    if not added:
        return 0.0, None  # nothing new: no project, no cost, whatever was quoted
    if quote_usd is None:
        return None, QUOTE_MISSING
    scope = canonical_features(quoted_scope)
    if not set(added) <= set(scope):
        return None, QUOTE_SCOPE_INCOMPLETE
    if set(scope) != set(added) and not incremental_scope_confirmed:
        return None, QUOTE_SCOPE_UNCONFIRMED
    return _finite("quote_usd", quote_usd, minimum=0.0), None


def grant_for(effective_cost_usd: float, share: float, cap_usd: float) -> float:
    """min(share x cost, cap, cost)."""
    cost = _finite("effective_cost_usd", effective_cost_usd, minimum=0.0)
    return min(_finite("grant_share", share, minimum=0.0, maximum=1.0) * cost, _finite("grant_cap_usd", cap_usd, minimum=0.0), cost)


# --------------------------------------------------------------------------- #
# Time value, homeowner premium-only view
# --------------------------------------------------------------------------- #


def pv_factor(horizon_years: int, discount_rate: float) -> float:
    """Sum of 1/(1+r)^t for t = 1..H; H itself when r is zero."""
    if isinstance(horizon_years, bool) or not isinstance(horizon_years, int) or not 1 <= horizon_years <= 30:
        raise PremiumError(f"horizon_years must be an integer from 1 to 30, got {horizon_years!r}")
    rate = _finite("discount_rate", discount_rate, minimum=0.0, maximum=1.0)
    if rate == 0:
        return float(horizon_years)
    return sum(1.0 / (1.0 + rate) ** t for t in range(1, horizon_years + 1))


def premium_only_payback_years(
    homeowner_upfront_usd: float | None, annual_savings_usd: float, is_project: bool
) -> tuple[float | None, str | None]:
    """Upfront over positive annual savings. Zero for a free project that saves money."""
    if not is_project:
        return None, "No new project."
    if homeowner_upfront_usd is None:
        return None, "Project cost unknown."
    if annual_savings_usd <= 0:
        return None, "No premium savings to recover the cost against."
    return homeowner_upfront_usd / annual_savings_usd, None


# --------------------------------------------------------------------------- #
# Fixture loading and validation
# --------------------------------------------------------------------------- #


@lru_cache
def load_credit_plan(path: str | None = None) -> dict:
    plan = json.loads((Path(path) if path else FIXTURES / "premium_credit_plan.json").read_text(encoding="utf-8"))
    validate_credit_plan(plan)
    return plan


@lru_cache
def load_policies(path: str | None = None) -> dict:
    book = json.loads((Path(path) if path else FIXTURES / "insurer_policies.json").read_text(encoding="utf-8"))
    for policy in book["policies"]:
        validate_policy(policy)
    ids = [p["policy_id"] for p in book["policies"]]
    if len(set(ids)) != len(ids):
        raise PremiumError("insurer_policies.json repeats a policy_id")
    properties = [p["property_id"] for p in book["policies"]]
    if len(set(properties)) != len(properties):
        raise PremiumError("insurer_policies.json associates one property with two policies")
    return book


@lru_cache
def load_insurer_demo(path: str | None = None) -> dict:
    demo = json.loads((Path(path) if path else FIXTURES / "insurer_demo.json").read_text(encoding="utf-8"))
    validate_program(demo["program"])
    return demo


def reset_caches() -> None:
    load_credit_plan.cache_clear()
    load_policies.cache_clear()
    load_insurer_demo.cache_clear()


def validate_credit_plan(plan: dict) -> None:
    for key in CREDIT_KEYS.values():
        if key not in plan["credit_plan"]:
            raise PremiumError(f"credit plan is missing the {key!r} tier")
        _finite(f"credit_plan[{key}]", plan["credit_plan"][key], minimum=0.0, maximum=1.0)
    if not plan["zone_rates"]:
        raise PremiumError("credit plan has no zone rates")
    for zone, rate in plan["zone_rates"].items():
        _finite(f"zone_rates[{zone}]", rate, minimum=0.0, maximum=1.0)


def validate_program(program: dict) -> None:
    _finite("grant_share", program["grant_share"], minimum=0.0, maximum=1.0)
    for field in ("grant_cap_usd", "inspection_usd_per_project", "fixed_setup_usd", "annual_admin_usd", "budget_usd"):
        _finite(field, program[field], minimum=0.0)
    pv_factor(program["horizon_years"], program["discount_rate"])


def validate_policy(policy: dict) -> None:
    """The record checks section 5 of the specification asks for."""
    pid = policy["policy_id"]
    for field in ("coverage_a_usd", "coverage_limit_usd", "rating_value_usd"):
        _finite(f"{pid}.{field}", policy[field], minimum=0.0)
        if policy[field] <= 0:
            raise PremiumError(f"{pid}.{field} must be positive, got {policy[field]}")
    deductible = policy["deductible"]
    if deductible["kind"] == "percent":
        _finite(f"{pid}.deductible.fraction", deductible["fraction"], minimum=0.0, maximum=1.0)
    elif deductible["kind"] == "dollar":
        _finite(f"{pid}.deductible.amount_usd", deductible["amount_usd"], minimum=0.0)
    else:
        raise PremiumError(f"{pid}.deductible.kind must be 'percent' or 'dollar', got {deductible['kind']!r}")
    if policy.get("annual_nonwind_premium_usd") is not None:
        _finite(f"{pid}.annual_nonwind_premium_usd", policy["annual_nonwind_premium_usd"], minimum=0.0)
    for preset_id, state in policy["states"].items():
        installed = canonical_features(state["installed_features"])
        credited = canonical_features(state["credited_features"])
        if not set(credited) <= set(installed):
            raise PremiumError(
                f"{pid} preset {preset_id}: credited features {credited} are not a subset of installed {installed}"
            )
    proposal = policy.get("proposal")
    if proposal is not None:
        canonical_features(proposal["requested_features"])
        canonical_features(proposal["quoted_scope"])
        if proposal.get("quote_usd") is not None:
            _finite(f"{pid}.proposal.quote_usd", proposal["quote_usd"], minimum=0.0)


# --------------------------------------------------------------------------- #
# Pricing one policy, then a program
# --------------------------------------------------------------------------- #


def price_policy(policy: dict, preset_id: str, plan: dict, program: dict, *, selected: bool = True) -> dict:
    """Current and resulting wind premium for one policy under one preset, with the
    project's cost, grant and homeowner premium-only figures.

    `selected=False` leaves the policy exactly as it is: no features added, no cost, no
    credit change. That is what an unselected proposal means, and also what a policy
    with no proposal at all means.
    """
    validate_policy(policy)
    state = policy["states"][preset_id]
    installed = canonical_features(state["installed_features"])
    credited = canonical_features(state["credited_features"])
    proposal = policy.get("proposal") if selected else None
    requested = canonical_features(proposal["requested_features"]) if proposal else ()

    transition = resolve_transition(installed, requested)
    is_project = bool(transition.features_added)
    # A completed project is credited for what it added on top of what was already
    # credited: installed-but-uncredited features stay uncredited (a credit-only
    # correction is a separate, explicit change).
    resulting_credited = tuple(sorted(set(credited) | set(transition.features_added)))

    rate = zone_rate(policy["rating_zone"], plan)
    current_credit = credit_for(credited, plan)
    result_credit = credit_for(resulting_credited, plan)
    uncredited = uncredited_wind_premium(policy["rating_value_usd"], rate)
    current_premium = uncredited * (1.0 - current_credit)
    result_premium = uncredited * (1.0 - result_credit)
    foregone = current_premium - result_premium
    nonwind = policy.get("annual_nonwind_premium_usd")

    if proposal:
        cost, reason = effective_cost(
            proposal.get("quote_usd"), proposal["quoted_scope"], transition.features_added,
            bool(proposal.get("incremental_scope_confirmed")),
        )
    else:
        cost, reason = 0.0, None
    grant = grant_for(cost, program["grant_share"], program["grant_cap_usd"]) if (cost is not None and is_project) else (0.0 if cost is not None else None)
    owner_upfront = (cost - grant) if cost is not None else None

    payback, payback_note = premium_only_payback_years(owner_upfront, foregone, is_project)
    factor = pv_factor(program["horizon_years"], program["discount_rate"])

    return {
        "policy_id": policy["policy_id"],
        "property_id": policy["property_id"],
        "preset_id": preset_id,
        "selected": bool(selected and proposal is not None),
        "is_project": is_project,
        "rating_zone": policy["rating_zone"],
        "rating_value_usd": policy["rating_value_usd"],
        "zone_rate": rate,
        "installed_features": list(installed),
        "credited_features": list(credited),
        "requested_features": list(requested),
        "features_added": list(transition.features_added),
        "resulting_features": list(transition.resulting_features),
        "resulting_credited_features": list(resulting_credited),
        "current_credit": current_credit,
        "result_credit": result_credit,
        "uncredited_wind_premium_usd": money(uncredited),
        "current_wind_premium_usd": money(current_premium),
        "result_wind_premium_usd": money(result_premium),
        "annual_premium_foregone_usd": money(foregone),
        "annual_nonwind_premium_usd": money(nonwind),
        "current_total_premium_usd": money(current_premium + nonwind) if nonwind is not None else None,
        "result_total_premium_usd": money(result_premium + nonwind) if nonwind is not None else None,
        "proposal_id": proposal["proposal_id"] if proposal else None,
        "quote_usd": money(proposal.get("quote_usd")) if proposal else None,
        "quoted_scope": list(canonical_features(proposal["quoted_scope"])) if proposal else [],
        "effective_cost_usd": money(cost),
        "cost_unavailable_reason": reason,
        "insurer_grant_usd": money(grant),
        "homeowner_upfront_usd": money(owner_upfront),
        "homeowner": {
            "annual_premium_savings_usd": money(foregone),
            "premium_only_payback_years": round(payback, 2) if payback is not None else None,
            "payback_note": payback_note,
            "ten_year_undiscounted_premium_net_usd": (
                money(WORKBOOK_UNDISCOUNTED_YEARS * foregone - owner_upfront) if owner_upfront is not None else None
            ),
            "premium_only_npv_usd": money(-owner_upfront + foregone * factor) if owner_upfront is not None else None,
        },
        # Full-precision values for callers that aggregate; never shown directly.
        "_exact": {"current": current_premium, "result": result_premium, "cost": cost, "grant": grant},
    }


def price_program(
    policies: list[dict],
    preset_id: str,
    plan: dict,
    program: dict,
    selected_proposal_ids: Iterable[str] | None = None,
) -> dict:
    """Every policy priced under one preset, with the program's costs charged once.

    `selected_proposal_ids` of None selects every proposal; an empty set selects none.
    Fixed setup is charged once, and only if at least one real project proceeds;
    inspections are charged per real project, so a no-op proposal costs nothing.
    Cost-dependent totals are null, and `complete` is false, whenever any selected
    project's cost is unavailable; the premium totals never depend on a quote and are
    always present.
    """
    validate_program(program)
    selected = None if selected_proposal_ids is None else set(selected_proposal_ids)
    known = {p["proposal"]["proposal_id"] for p in policies if p.get("proposal")}
    if selected is not None and not selected <= known:
        raise PremiumError(f"unknown proposal ids: {sorted(selected - known)}")

    rows = []
    for policy in policies:
        proposal = policy.get("proposal")
        is_selected = proposal is not None and (selected is None or proposal["proposal_id"] in selected)
        rows.append(price_policy(policy, preset_id, plan, program, selected=is_selected))

    unavailable = [
        {"policy_id": r["policy_id"], "proposal_id": r["proposal_id"], "reason": r["cost_unavailable_reason"]}
        for r in rows
        if r["cost_unavailable_reason"]
    ]
    complete = not unavailable
    projects = [r for r in rows if r["is_project"]]
    exact_current = sum(r["_exact"]["current"] for r in rows)
    exact_result = sum(r["_exact"]["result"] for r in rows)
    foregone = exact_current - exact_result

    if complete:
        project_cost = sum(r["_exact"]["cost"] for r in projects)
        grants = sum(r["_exact"]["grant"] for r in projects)
        inspections = program["inspection_usd_per_project"] * len(projects)
        setup = program["fixed_setup_usd"] if projects else 0.0
        insurer_upfront = grants + inspections + setup
        homeowner_upfront = project_cost - grants
        cost_totals = {
            "project_cost_usd": money(project_cost),
            "insurer_grants_usd": money(grants),
            "inspections_usd": money(inspections),
            "fixed_setup_usd": money(setup),
            "insurer_upfront_usd": money(insurer_upfront),
            "homeowner_upfront_usd": money(homeowner_upfront),
            "owner_funded_ten_year_undiscounted_premium_net_usd": money(WORKBOOK_UNDISCOUNTED_YEARS * foregone - project_cost),
            "homeowner_ten_year_undiscounted_premium_net_usd": money(WORKBOOK_UNDISCOUNTED_YEARS * foregone - homeowner_upfront),
        }
    else:
        cost_totals = {key: None for key in (
            "project_cost_usd", "insurer_grants_usd", "inspections_usd", "fixed_setup_usd", "insurer_upfront_usd",
            "homeowner_upfront_usd", "owner_funded_ten_year_undiscounted_premium_net_usd",
            "homeowner_ten_year_undiscounted_premium_net_usd",
        )}

    for row in rows:
        del row["_exact"]

    return {
        "schema_version": SCHEMA_VERSION,
        "preset_id": preset_id,
        "plan_id": plan.get("plan_id"),
        "program": dict(program),
        "policy_count": len(rows),
        "project_count": len(projects),
        "selected_proposal_ids": sorted(r["proposal_id"] for r in rows if r["selected"]),
        "complete": complete,
        "unavailable": unavailable,
        "policies": rows,
        "totals": {
            "current_wind_premium_usd": money(exact_current),
            "result_wind_premium_usd": money(exact_result),
            "annual_premium_foregone_usd": money(foregone),
            "annual_admin_usd": money(program["annual_admin_usd"]),
            "annual_nonwind_premium_usd": (
                money(sum(p["annual_nonwind_premium_usd"] for p in policies))
                if all(p.get("annual_nonwind_premium_usd") is not None for p in policies)
                else None
            ),
            **cost_totals,
        },
        "pv_factor": pv_factor(program["horizon_years"], program["discount_rate"]),
        "units": {"money": "USD", "credits_and_rates": "fraction of 1"},
        "notes": [
            "Wind premium = rating value x zone rate x (1 - credit for the credited feature set). "
            "The credit is looked up for the feature union, never summed, and is never applied "
            "to Coverage A or to the non-wind premium.",
            "Effective project cost covers new features only; a proposal adding nothing is not "
            "a project and incurs no grant, inspection or setup charge.",
            "Premium figures describe the discount concession, not claims: avoided payouts come "
            "from the loss engine and are combined with these totals elsewhere.",
        ],
    }
