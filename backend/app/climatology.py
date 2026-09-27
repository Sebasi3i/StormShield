"""Expected yearly losses from an unselected sample of simulated storms.

`fixtures/storm_climatology.json` (built by scripts/build_storm_climatology.py) holds
the peak gust that each of thousands of randomly generated Atlantic storms brings to
each demo property. Because the storms were not chosen to hit anything, the sample is
a fair one, and a storms-per-year rate for the same population turns "average loss per
simulated storm" into "expected loss per year". That is the number the invented event
probability in the insurer's annual model stood in for, and this module is what
replaces it.

Pricing is the claims engine's arithmetic, vectorised: the curve for each property's
physical state, interpolated at every simulated gust at once, then value, deductible
and limit. Results agree with claims.py's per-row figures to the cent for any single
storm, and a test holds them to that.

Pure like its neighbours: fixture and dicts in, dicts out.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np

from . import claims, premium
from .claims import Curve, Policy
from .mitigation_states import PropertyState, curve_for_state

FIXTURES = Path(__file__).resolve().parent / "fixtures"

SCHEMA_VERSION = "insurer-demo-v1"
RETURN_PERIODS_YEARS = (10, 25, 50, 100)


class ClimatologyError(ValueError):
    """The fixture is missing, malformed, or does not cover what was asked of it."""


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


@lru_cache
def load_climatology(path: str | None = None) -> dict:
    fixture = Path(path) if path else FIXTURES / "storm_climatology.json"
    if not fixture.exists():
        raise ClimatologyError(
            f"{fixture.name} has not been built; run scripts/build_storm_climatology.py"
        )
    data = json.loads(fixture.read_text(encoding="utf-8"))
    validate_climatology(data)
    return data


def reset_caches() -> None:
    load_climatology.cache_clear()


def validate_climatology(data: dict, curve_set: dict | None = None) -> None:
    columns = data["gust_columns"]
    if not columns or len(set(columns)) != len(columns):
        raise ClimatologyError("gust_columns must list each property id once")
    if not data["storms"]:
        raise ClimatologyError("the climatology has no storms")
    for storm in data["storms"]:
        gusts = storm["gusts_mph"]
        if len(gusts) != len(columns) or any(not math.isfinite(g) or g < 0 for g in gusts):
            raise ClimatologyError(f"storm {storm['storm_id']}: gusts must be {len(columns)} finite, nonnegative numbers")
    rates = data["storms_per_year"]
    if rates["default"] not in rates or rates[rates["default"]]["storms_per_year"] <= 0:
        raise ClimatologyError("storms_per_year.default must name a positive rate")
    curve_set = curve_set or claims.load_curve_set()
    upper = min(c.max_supported_wind for c in curve_set["curves"].values())
    if data["max_gust_mph"] > upper:
        raise ClimatologyError(
            f"the climatology's largest gust ({data['max_gust_mph']} mph) is above the curve set's "
            f"supported range ({upper} mph); extend the curves rather than clamping"
        )


def storms_per_year(data: dict, override: float | None = None) -> float:
    if override is not None:
        if not isinstance(override, (int, float)) or isinstance(override, bool) or not math.isfinite(override) or override <= 0:
            raise ClimatologyError(f"storms_per_year must be a positive number, got {override!r}")
        return float(override)
    rates = data["storms_per_year"]
    return float(rates[rates["default"]]["storms_per_year"])


def gust_matrix(data: dict, property_ids: Iterable[str]) -> np.ndarray:
    """Gusts as (storms x requested properties), in the requested order."""
    columns = {pid: i for i, pid in enumerate(data["gust_columns"])}
    wanted = list(property_ids)
    missing = [pid for pid in wanted if pid not in columns]
    if missing:
        raise ClimatologyError(
            f"the climatology has no gusts for {missing}; it covers {data['gust_columns']}. "
            "Rebuild it with scripts/build_storm_climatology.py after adding a property."
        )
    matrix = np.array([s["gusts_mph"] for s in data["storms"]], dtype=float)
    return matrix[:, [columns[pid] for pid in wanted]]


# --------------------------------------------------------------------------- #
# Vectorised pricing
# --------------------------------------------------------------------------- #


def _fractions(curve: Curve, gusts: np.ndarray) -> np.ndarray:
    winds = np.array([w for w, _ in curve.points], dtype=float)
    losses = np.array([d for _, d in curve.points], dtype=float)
    if gusts.max(initial=0.0) > curve.max_supported_wind:
        raise claims.UnsupportedInputError(
            f"a simulated gust of {gusts.max():.1f} mph is above the supported range of curve "
            f"{curve.curve_id} ({curve.max_supported_wind} mph)"
        )
    return np.interp(gusts, winds, losses)


def _price(value: float, curve: Curve, gusts: np.ndarray, policy: Policy) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(damage, payout, uninsured) per storm for one property in one physical state."""
    damage = value * _fractions(curve, gusts)
    payout = np.clip(damage - policy.deductible_usd, 0.0, policy.coverage_limit_usd)
    return damage, payout, damage - payout


def _money(value: float) -> float:
    return premium.money(float(value))


def _return_periods(per_storm: np.ndarray, rate: float) -> dict:
    """The per-storm value exceeded, on average, once per T years.

    A storm is a Poisson arrival at `rate` per year and a share p of storms exceed x,
    so exceedances arrive at rate x p per year: once per T years when p = 1/(rate x T).
    Null when the sample is too small to resolve that share.
    """
    ordered = np.sort(per_storm)[::-1]
    n = len(ordered)
    out = {}
    for years in RETURN_PERIODS_YEARS:
        share = 1.0 / (rate * years)
        k = int(math.floor(share * n))
        out[f"once_per_{years}_years_usd"] = _money(ordered[k - 1]) if k >= 1 else None
    return out


def expected_annual(
    states: list[PropertyState],
    policies: list[Policy],
    data: dict,
    *,
    storms_per_year_override: float | None = None,
    curve_set: dict | None = None,
) -> dict:
    """Expected yearly repair cost, payout and uninsured damage for the book as it is
    and as its projects leave it, from the whole simulated sample.

    Per storm, each property's current and resulting state is priced at that storm's
    gust; the portfolio is summed per storm; the mean per storm times the storms-per-
    year rate is the expected annual figure. Also the share of storms that cause any
    loss, the chance of at least one paying storm in a year, per-property figures
    (so a caller can attribute a program's benefit policy by policy), the largest
    simulated storms and rough return-period values.
    """
    curve_set = curve_set or claims.load_curve_set()
    curves = curve_set["curves"]
    validate_climatology(data, curve_set)
    rate = storms_per_year(data, storms_per_year_override)
    policies_by_id = {p.property_id: p for p in policies}
    gusts = gust_matrix(data, [s.property_id for s in states])
    n_storms = gusts.shape[0]

    totals = {k: np.zeros(n_storms) for k in ("current_damage", "result_damage", "current_payout", "result_payout", "current_uninsured", "result_uninsured")}
    per_property: dict[str, dict] = {}
    for i, state in enumerate(states):
        policy = policies_by_id.get(state.property_id)
        if policy is None:
            raise claims.MissingDataError(f"no policy for property {state.property_id}")
        current_curve, _ = curve_for_state(curves, state.vulnerability_class, state.current_features, state.roof_shape)
        result_curve, _ = curve_for_state(curves, state.vulnerability_class, state.resulting_features, state.roof_shape)
        column = gusts[:, i]
        d0, p0, u0 = _price(state.replacement_cost_usd, current_curve, column, policy)
        d1, p1, u1 = _price(state.replacement_cost_usd, result_curve, column, policy)
        for key, arr in zip(totals, (d0, d1, p0, p1, u0, u1)):
            totals[key] += arr
        per_property[state.property_id] = {
            "property_id": state.property_id,
            "current_features": list(state.current_features),
            "resulting_features": list(state.resulting_features),
            "current_curve_id": current_curve.curve_id,
            "result_curve_id": result_curve.curve_id,
            "share_of_storms_with_damage": round(float((d0 > 0).mean()), 4),
            "share_of_storms_with_payout": round(float((p0 > 0).mean()), 4),
            "expected_annual_repair_cost_usd": _money(rate * d0.mean()),
            "expected_annual_repair_cost_after_usd": _money(rate * d1.mean()),
            "expected_annual_payout_usd": _money(rate * p0.mean()),
            "expected_annual_payout_after_usd": _money(rate * p1.mean()),
            "expected_annual_avoided_payout_usd": _money(rate * (p0 - p1).mean()),
            "expected_annual_avoided_repair_cost_usd": _money(rate * (d0 - d1).mean()),
            "expected_annual_avoided_uninsured_damage_usd": _money(rate * (u0 - u1).mean()),
            "_exact_avoided_payout": float(rate * (p0 - p1).mean()),
        }

    cur_pay, res_pay = totals["current_payout"], totals["result_payout"]
    share_paying_current = float((cur_pay > 0).mean())
    share_paying_result = float((res_pay > 0).mean())
    top = np.argsort(cur_pay)[::-1][:5]
    exact = {
        "current_payout": float(rate * cur_pay.mean()),
        "result_payout": float(rate * res_pay.mean()),
        "current_uninsured": float(rate * totals["current_uninsured"].mean()),
        "result_uninsured": float(rate * totals["result_uninsured"].mean()),
        "mean_avoided_payout_per_storm": float((cur_pay - res_pay).mean()),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "climatology_id": data["climatology_id"],
        "evidence_status": data["evidence_status"],
        "sample_storms": n_storms,
        "storms_per_year": rate,
        "storms_per_year_basis": data["storms_per_year"],
        "share_of_storms": {
            "with_any_repair_cost": round(float((totals["current_damage"] > 0).mean()), 4),
            "with_any_payout_current": round(share_paying_current, 4),
            "with_any_payout_after": round(share_paying_result, 4),
        },
        "probability_of_a_year_with_any_payout": {
            "current": round(1.0 - math.exp(-rate * share_paying_current), 4),
            "after": round(1.0 - math.exp(-rate * share_paying_result), 4),
            "basis": "storms arrive as a Poisson process at storms_per_year; 1 - exp(-rate x share of storms with a payout)",
        },
        "expected_annual": {
            "current_repair_cost_usd": _money(rate * totals["current_damage"].mean()),
            "after_repair_cost_usd": _money(rate * totals["result_damage"].mean()),
            "current_payout_usd": _money(exact["current_payout"]),
            "after_payout_usd": _money(exact["result_payout"]),
            "current_uninsured_damage_usd": _money(exact["current_uninsured"]),
            "after_uninsured_damage_usd": _money(exact["result_uninsured"]),
            "avoided_repair_cost_usd": _money(rate * (totals["current_damage"] - totals["result_damage"]).mean()),
            "avoided_payout_usd": _money(exact["current_payout"] - exact["result_payout"]),
            "avoided_uninsured_damage_usd": _money(exact["current_uninsured"] - exact["result_uninsured"]),
        },
        "mean_per_storm": {
            "current_payout_usd": _money(cur_pay.mean()),
            "after_payout_usd": _money(res_pay.mean()),
            "avoided_payout_usd": _money(exact["mean_avoided_payout_per_storm"]),
        },
        "return_periods_current_payout": _return_periods(cur_pay, rate),
        "largest_simulated_storms": [
            {
                "storm_id": data["storms"][int(j)]["storm_id"],
                "peak_wind_kt": data["storms"][int(j)]["peak_wind_kt"],
                "max_gust_at_a_property_mph": round(float(gusts[int(j)].max()), 1),
                "current_payout_usd": _money(cur_pay[int(j)]),
                "after_payout_usd": _money(res_pay[int(j)]),
            }
            for j in top
            if cur_pay[int(j)] > 0
        ],
        "per_property": per_property,
        "_exact": exact,
        "notes": [
            "Expected values over an unselected sample of simulated storms, times a storms-per-year rate for the same population. Not calibrated to any insurer's claims experience.",
            "The wind and damage models carry their own errors (see the wind validation and the Hazus provenance); the sample adds statistical noise that shrinks with its size.",
            "Each storm is priced independently, with the home repaired between storms; a year with two storms is two independent events.",
        ],
    }


# --------------------------------------------------------------------------- #
# The map's question: an average year for a property, per upgrade
# --------------------------------------------------------------------------- #


def average_year_for_properties(
    properties: list[claims.Property],
    policies: list[Policy],
    property_id_map: dict[str, str],
    data: dict,
    *,
    storms_per_year_override: float | None = None,
    curve_set: dict | None = None,
) -> dict:
    """Expected yearly repair cost and insurance cover for each property as it is, and
    what each available upgrade would save per year.

    `property_id_map` maps a caller's property id (the map's "1") to the climatology's
    ("P001"). The class baseline is the current state, as in claims.compute_losses.
    """
    curve_set = curve_set or claims.load_curve_set()
    curves = curve_set["curves"]
    validate_climatology(data, curve_set)
    rate = storms_per_year(data, storms_per_year_override)
    policies_by_id = {p.property_id: p for p in policies}
    ids = []
    for prop in properties:
        mapped = property_id_map.get(prop.property_id, prop.property_id)
        ids.append(mapped)
    gusts = gust_matrix(data, ids)

    rows = []
    for i, prop in enumerate(properties):
        policy = policies_by_id[prop.property_id]
        baseline = claims.find_curve(curves, prop.vulnerability_class, claims.BASELINE, prop.roof_shape)
        column = gusts[:, i]
        d0, p0, u0 = _price(prop.replacement_cost_usd, baseline, column, policy)
        upgrades = {}
        for upgrade_id in claims.eligible_upgrades(curves, prop.vulnerability_class):
            curve = claims.find_curve(curves, prop.vulnerability_class, upgrade_id, prop.roof_shape)
            d1, p1, u1 = _price(prop.replacement_cost_usd, curve, column, policy)
            upgrades[upgrade_id] = {
                "features_added": sorted(set(curve.features or ()) - set(baseline.features or ())) if curve.features is not None else None,
                "expected_annual_repair_cost_usd": _money(rate * d1.mean()),
                "expected_annual_payout_usd": _money(rate * p1.mean()),
                "expected_annual_avoided_repair_cost_usd": _money(rate * (d0 - d1).mean()),
                "expected_annual_avoided_payout_usd": _money(rate * (p0 - p1).mean()),
                "expected_annual_avoided_uninsured_damage_usd": _money(rate * (u0 - u1).mean()),
            }
        share_damage = float((d0 > 0).mean())
        rows.append(
            {
                "property_id": prop.property_id,
                "climatology_property_id": ids[i],
                "installed_features": list(baseline.features) if baseline.features is not None else None,
                "share_of_storms_with_damage": round(share_damage, 4),
                "probability_of_damage_in_a_year": round(1.0 - math.exp(-rate * share_damage), 4),
                "probability_of_a_claim_in_a_year": round(1.0 - math.exp(-rate * float((p0 > 0).mean())), 4),
                "expected_annual_repair_cost_usd": _money(rate * d0.mean()),
                "expected_annual_payout_usd": _money(rate * p0.mean()),
                "expected_annual_uninsured_damage_usd": _money(rate * u0.mean()),
                "return_periods_repair_cost": _return_periods(d0, rate),
                "upgrades": upgrades,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "climatology_id": data["climatology_id"],
        "evidence_status": data["evidence_status"],
        "sample_storms": gusts.shape[0],
        "storms_per_year": rate,
        "storms_per_year_basis": data["storms_per_year"],
        "curve_set_id": curve_set["curve_set_id"],
        "properties": rows,
        "totals": {
            "expected_annual_repair_cost_usd": _money(sum(r["expected_annual_repair_cost_usd"] for r in rows)),
            "expected_annual_payout_usd": _money(sum(r["expected_annual_payout_usd"] for r in rows)),
        },
        "notes": [
            "An average year, not a forecast: each figure is the mean over thousands of simulated storms times how many storms a year the record holds. Most years see none of this; a bad year sees far more.",
            "Insurance cover is the repair cost above a 5% deductible, up to the home's value, as in the storm report.",
        ],
    }
