"""Physical-state transitions: the home as it is, and the home after its upgrades, priced
on the same wind.

`claims.compute_losses` answers the map's question: for each property, what would each
single upgrade have saved on this storm, against the class baseline. The sample insurer
asks a different one: this policy's home currently HAS some features and would END UP
with some others, so what are its damage and payout in each state, and what moves? The
difference matters when a home already has straps and is quoted shutters: the current
curve is the straps curve, not the baseline, and the resulting curve is the pair, not
the sum of two single-feature reductions.

So this module resolves a physical state (class, installed features, roof shape) to a
curve by lookup on the `features` every curve now carries, and prices one current/
resulting pair per policy and event, no-op pairs included. Baseline figures for the
same state and exposure are identical to `claims.compute_losses`'s to the cent: the
arithmetic (`damage_fraction`, `damage_usd`, `payout_usd`, `_money`) is claims.py's own,
called once per state.

Pure, like claims.py and premium.py: dataclass-ish inputs in, dicts out, no HTTP.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple

from . import claims, premium
from .claims import Curve, EngineError, MissingDataError, Policy, WindExposure

SCHEMA_VERSION = "insurer-demo-v1"

# The two feature sets the two vulnerability classes can legitimately be in, for the
# error message when a state has no curve.
FEATURES = premium.FEATURES


class PhysicalStateError(EngineError):
    """A physical state the curve set has no curve for, such as a post-2002 home
    recorded without the roof straps its class includes by construction."""


class PropertyState(NamedTuple):
    """One insured building in its current state and the state a project leaves it in.

    `current_features` and `resulting_features` are what is physically installed, not
    what is credited; a policy that adds nothing has the two equal, and still gets a
    row per event, because "no change" is a result and a missing row is missing data.
    """

    property_id: str
    replacement_cost_usd: float
    vulnerability_class: str
    roof_shape: str
    current_features: tuple[str, ...]
    resulting_features: tuple[str, ...]


# --------------------------------------------------------------------------- #
# State -> curve
# --------------------------------------------------------------------------- #


def canonical_features(features: Iterable[str] | None) -> tuple[str, ...]:
    return premium.canonical_features(features)


def curves_by_state(curves: dict[tuple[str, str, str], Curve]) -> dict[tuple[str, tuple[str, ...], str], Curve]:
    """(class, installed features, roof shape) -> curve, for every curve that says
    which features its building has."""
    index: dict[tuple[str, tuple[str, ...], str], Curve] = {}
    for curve in curves.values():
        if curve.features is None:
            continue
        key = (curve.vulnerability_class, tuple(curve.features), curve.roof_shape)
        if key in index:
            raise claims.CurveError(
                f"two curves describe {key[0]} with {list(key[1]) or 'no features'} on a {key[2]} "
                f"roof: {index[key].curve_id} and {curve.curve_id}"
            )
        index[key] = curve
    return index


def curve_for_state(
    curves: dict[tuple[str, str, str], Curve],
    vulnerability_class: str,
    features: Iterable[str] | None,
    roof_shape: str | None,
) -> tuple[Curve, str | None]:
    """The curve for this class in this physical state, with the roof-shape fallback
    reason if the declared shape had no curve of its own.

    A state the set has no curve for is an error, not a silent substitution: a
    post_fbc_2002 home "without straps" is a conflict with the class archetype that
    the caller must normalize (see the app-consistent preset), never something to
    price against a different building.
    """
    installed = canonical_features(features)
    index = curves_by_state(curves)
    shape = claims._resolve_roof_shape(roof_shape)
    curve = index.get((vulnerability_class, installed, shape))
    fallback = None
    if curve is None and shape != claims.BLENDED_ROOF_SHAPE:
        curve = index.get((vulnerability_class, installed, claims.BLENDED_ROOF_SHAPE))
        if curve is not None:
            fallback = f"no {shape} curve for {vulnerability_class} with {list(installed) or 'no features'}; used the blended curve"
    if curve is None:
        available = sorted({list(f) and ", ".join(f) or "none" for c, f, _ in index if c == vulnerability_class})
        if not index:
            raise PhysicalStateError(
                "this curve set records no 'features' on its curves, so physical states cannot be "
                "priced with it; rebuild it with scripts/build_damage_curves.py"
            )
        raise PhysicalStateError(
            f"no curve for {vulnerability_class} with {list(installed) or 'no features'} installed. "
            f"States this curve set has for that class: {available}. A post_fbc_2002 home without "
            "roof_straps conflicts with the class archetype; normalize the state rather than "
            "pricing it against a different building."
        )
    return curve, fallback


# --------------------------------------------------------------------------- #
# Building inputs from the insurer fixture
# --------------------------------------------------------------------------- #


def policy_from_record(record: dict) -> Policy:
    """A claims.Policy from an insurer_policies.json record: the deductible in dollars
    against its documented base, and the limit."""
    deductible = record["deductible"]
    if deductible["kind"] == "percent":
        amount = claims.deductible_from_percent(deductible["fraction"], record["coverage_a_usd"])
        return Policy(
            property_id=record["property_id"],
            deductible_usd=amount,
            coverage_limit_usd=record["coverage_limit_usd"],
            deductible_basis="percent_of_coverage_a",
            deductible_percent=deductible["fraction"],
            deductible_base_usd=record["coverage_a_usd"],
        )
    if deductible["kind"] == "dollar":
        return Policy(
            property_id=record["property_id"],
            deductible_usd=float(deductible["amount_usd"]),
            coverage_limit_usd=record["coverage_limit_usd"],
        )
    raise EngineError(f"{record['policy_id']}: unknown deductible kind {deductible['kind']!r}")


def state_from_record(record: dict, preset_id: str, *, selected: bool = True) -> PropertyState:
    """A PropertyState from an insurer_policies.json record under one preset.

    `selected=False` (an unselected proposal) leaves the home as it is.
    """
    prop = record["property"]
    installed = canonical_features(record["states"][preset_id]["installed_features"])
    requested = canonical_features(record["proposal"]["requested_features"]) if (selected and record.get("proposal")) else ()
    transition = premium.resolve_transition(installed, requested)
    return PropertyState(
        property_id=record["property_id"],
        replacement_cost_usd=float(prop["replacement_cost_usd"]),
        vulnerability_class=prop["vulnerability_class"],
        roof_shape=prop.get("roof_shape", "unknown"),
        current_features=installed,
        resulting_features=transition.resulting_features,
    )


# --------------------------------------------------------------------------- #
# The calculation
# --------------------------------------------------------------------------- #


def _state_losses(
    replacement_cost_usd: float, curve: Curve, exposure: WindExposure, policy: Policy
) -> tuple[float, float, float]:
    """(damage, payout, uninsured damage) for one state on one exposure, full precision."""
    fraction = claims.damage_fraction(curve, exposure.peak_gust_mph, exposure.wind_metric)
    damage = claims.damage_usd(replacement_cost_usd, fraction)
    payout = claims.payout_usd(damage, policy.deductible_usd, policy.coverage_limit_usd)
    return damage, payout, damage - payout


def compute_transitions(
    states: list[PropertyState],
    policies: list[Policy],
    exposures: list[WindExposure],
    storm_ids: list[str],
    *,
    curve_set: dict | None = None,
) -> dict:
    """One current/resulting pair per policy and event.

    Every declared storm x property gets exactly one row, whether or not the state
    changes, so a portfolio's current payout is the sum of one figure per policy and
    event and never depends on how many proposals were on the table. Missing wind for
    a declared pair is an error, as in claims.compute_losses.
    """
    curve_set = curve_set or claims.load_curve_set()
    curves = curve_set["curves"]

    by_id = {s.property_id: s for s in states}
    if len(by_id) != len(states):
        raise claims.DuplicateRowError("the same property_id appears twice in states")
    policies_by_id = {p.property_id: p for p in policies}
    if len(policies_by_id) != len(policies):
        raise claims.DuplicateRowError("the same property_id appears twice in policies")
    indexed_exposures = claims._index_exposures(exposures)

    warnings: list[str] = []
    selection: list[dict] = []
    chosen: dict[str, tuple[Curve, Curve]] = {}
    for state in states:
        if state.property_id not in policies_by_id:
            raise MissingDataError(f"no policy for property {state.property_id}")
        declared = (state.roof_shape or "").strip().lower()
        if declared not in ("", "unknown", *claims.KNOWN_ROOF_SHAPES):
            warnings.append(
                f"property {state.property_id}: declared roof_shape {state.roof_shape!r} is not a "
                "recognised value (expected 'gable', 'hip', 'unknown', or empty/missing); used the blended curve"
            )
        current_curve, current_fallback = curve_for_state(
            curves, state.vulnerability_class, state.current_features, state.roof_shape
        )
        result_curve, result_fallback = curve_for_state(
            curves, state.vulnerability_class, state.resulting_features, state.roof_shape
        )
        chosen[state.property_id] = (current_curve, result_curve)
        reasons = [r for r in (current_fallback, result_fallback) if r]
        selection.append(
            {
                "property_id": state.property_id,
                "vulnerability_class": state.vulnerability_class,
                "declared_roof_shape": state.roof_shape,
                "resolved_roof_shape": current_curve.roof_shape,
                "current_features": list(state.current_features),
                "resulting_features": list(state.resulting_features),
                "current_curve_id": current_curve.curve_id,
                "result_curve_id": result_curve.curve_id,
                "no_op": state.current_features == state.resulting_features,
                "fallback_reason": sorted(set(reasons)) or None,
            }
        )

    rows: list[dict] = []
    totals: dict[str, dict[str, float]] = {}
    for storm_id in storm_ids:
        sums = {k: 0.0 for k in ("current_damage", "result_damage", "current_payout", "result_payout", "current_uninsured", "result_uninsured")}
        for state in states:
            exposure = indexed_exposures.get((storm_id, state.property_id))
            if exposure is None:
                raise MissingDataError(
                    f"no wind exposure for storm {storm_id} property {state.property_id}. "
                    "A missing row means missing data, not zero loss: supply an explicit zero-exposure row."
                )
            policy = policies_by_id[state.property_id]
            current_curve, result_curve = chosen[state.property_id]
            d0, p0, u0 = _state_losses(state.replacement_cost_usd, current_curve, exposure, policy)
            d1, p1, u1 = _state_losses(state.replacement_cost_usd, result_curve, exposure, policy)
            if p1 - p0 > 1e-9:
                warnings.append(
                    f"storm {storm_id} property {state.property_id}: the resulting state pays out more "
                    f"than the current one. Review curves {current_curve.curve_id} and {result_curve.curve_id}."
                )
            rows.append(
                {
                    "storm_id": storm_id,
                    "property_id": state.property_id,
                    "peak_gust_mph": round(exposure.peak_gust_mph, 1),
                    "current_features": list(state.current_features),
                    "resulting_features": list(state.resulting_features),
                    "current_curve_id": current_curve.curve_id,
                    "result_curve_id": result_curve.curve_id,
                    "no_op": state.current_features == state.resulting_features,
                    "deductible_usd": claims._money(policy.deductible_usd),
                    "coverage_limit_usd": claims._money(policy.coverage_limit_usd),
                    "current_damage_usd": claims._money(d0),
                    "result_damage_usd": claims._money(d1),
                    "current_payout_usd": claims._money(p0),
                    "result_payout_usd": claims._money(p1),
                    "current_uninsured_damage_usd": claims._money(u0),
                    "result_uninsured_damage_usd": claims._money(u1),
                    "avoided_damage_usd": claims._money(d0 - d1),
                    "avoided_payout_usd": claims._money(p0 - p1),
                    "avoided_uninsured_damage_usd": claims._money(u0 - u1),
                }
            )
            for key, value in zip(sums, (d0, d1, p0, p1, u0, u1)):
                sums[key] += value
        totals[storm_id] = {
            "current_damage_usd": claims._money(sums["current_damage"]),
            "result_damage_usd": claims._money(sums["result_damage"]),
            "current_payout_usd": claims._money(sums["current_payout"]),
            "result_payout_usd": claims._money(sums["result_payout"]),
            "current_uninsured_damage_usd": claims._money(sums["current_uninsured"]),
            "result_uninsured_damage_usd": claims._money(sums["result_uninsured"]),
            "avoided_damage_usd": claims._money(sums["current_damage"] - sums["result_damage"]),
            "avoided_payout_usd": claims._money(sums["current_payout"] - sums["result_payout"]),
            "avoided_uninsured_damage_usd": claims._money(sums["current_uninsured"] - sums["result_uninsured"]),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "curve_set_id": curve_set["curve_set_id"],
        "wind_metric": curve_set["wind_metric"],
        "storm_ids": list(storm_ids),
        "property_ids": [s.property_id for s in states],
        "curve_selection": selection,
        "rows": rows,
        "totals_by_storm": totals,
        "warnings": warnings,
        "notes": [
            "One row per storm and property: the home as it is and as its project leaves it, "
            "on the same wind. No-op pairs are rows too, with zero deltas.",
            "payout = min(coverage_limit, max(0, damage - deductible)); uninsured damage = "
            "damage - payout. A deductible change moves loss between payout and uninsured "
            "damage without changing damage.",
            "Each event is priced independently (the deductible resets, the building is "
            "repaired between events); nothing here attaches an annual probability.",
        ],
    }
