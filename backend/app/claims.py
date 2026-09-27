"""Damage and insurance payout engine: wind at a home -> damage -> insurer payout.

Scope is the CS Developer 1 brief, shared response contract version 1.1. One
residential building per property, direct wind damage to the building, one simplified
coverage and deductible, mutually exclusive upgrades. Each row is an INDIVIDUAL storm,
not a simulated year: losses are computed independently per storm, the deductible
resets, and the building is assumed fully repaired before each event. Annual event
rates are Adryel's input and Developer 2's to apply - nothing here attaches an annual
probability to anything.

Results are illustrative gross insurer payouts before reinsurance.

Nothing in this module imports FastAPI or touches HTTP. It takes dataclasses and
returns dicts, so the same functions serve the endpoint, the tests and any future
batch job. Two rules that are easy to break and expensive to get wrong:

  - Curves are supplied, never invented. This module interpolates what the
    math/research team gives it and refuses what it cannot interpret.
  - Do not round in the middle. Round once, on the way out, to cents.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

SCHEMA_VERSION = "1.1"

FIXTURES = Path(__file__).resolve().parent / "fixtures"

BASELINE = "baseline"

# Results are labeled with this so no consumer can mistake them for a net figure.
RESULT_BASIS = "illustrative gross insurer payouts before reinsurance"


# --------------------------------------------------------------------------- #
# Errors
#
# Every failure names the row and the field, because the alternative - a silently
# dropped property or a guessed conversion - produces a number that looks fine and is
# wrong. A 422 with "no curve for class pre_fbc_2002 upgrade shutters" is recoverable;
# a quietly missing row is not.
# --------------------------------------------------------------------------- #


class EngineError(ValueError):
    """Base class for every rejection this engine raises."""


class CurveError(EngineError):
    """A curve is missing, malformed, or not monotonic."""


class UnsupportedInputError(EngineError):
    """Wind outside the curve's supported range. Never extrapolated silently."""


class MissingDataError(EngineError):
    """A required property, policy or wind exposure row is absent."""


class DuplicateRowError(EngineError):
    """The same storm/property appears twice in the inputs."""


class UnitMismatchError(EngineError):
    """The wind metric supplied does not match the metric the curve is defined on."""


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


class Curve(NamedTuple):
    """A vulnerability curve: ordered (wind, damage fraction) pairs.

    `wind_metric` is carried on every curve and checked against the wind metric of the
    exposure. The gust averaging duration is part of the metric name: a 3-second gust
    and a 1-minute sustained wind at the same speed are different hazards, and
    converting between them is a modeling decision, not arithmetic this engine may
    perform on its own.
    """

    curve_id: str
    vulnerability_class: str
    upgrade_id: str
    wind_metric: str
    points: tuple[tuple[float, float], ...]
    evidence_status: str
    source_note: str

    @property
    def max_supported_wind(self) -> float:
        return self.points[-1][0]


class Property(NamedTuple):
    property_id: str
    replacement_cost_usd: float
    vulnerability_class: str


class Policy(NamedTuple):
    """One simplified coverage.

    `deductible_usd` is always a dollar amount by the time it reaches this engine. A
    percentage deductible is converted by `deductible_from_percent` against its
    documented policy base - never against the damage amount, which would be a
    different and much cheaper product than the one Finance described.
    """

    property_id: str
    deductible_usd: float
    coverage_limit_usd: float
    deductible_basis: str = "dollar"
    deductible_percent: float | None = None
    deductible_base_usd: float | None = None


class WindExposure(NamedTuple):
    """Property-level peak gust for one storm.

    Produced upstream by the wind field model (see `app/wind.py`), never by this
    engine. Zero exposure is represented by a row with a low or zero gust, not by
    omitting the row: an absent row means missing data.
    """

    storm_id: str
    property_id: str
    peak_gust_mph: float
    wind_metric: str


# --------------------------------------------------------------------------- #
# Curve loading and validation
# --------------------------------------------------------------------------- #


def validate_curve(curve: Curve) -> None:
    """Reject a curve this engine cannot interpolate on.

    Checks the brief's requirements: fractions inside [0,1], a zero-wind/zero-damage
    anchor, strictly increasing wind, and nondecreasing damage. A curve that dips as
    wind rises is far more likely to be a data-entry error than a physical finding,
    and interpolating it would produce a negative avoided payout that looks like a
    modeling insight.
    """
    if len(curve.points) < 2:
        raise CurveError(f"curve {curve.curve_id} needs at least two points")

    first_wind, first_damage = curve.points[0]
    if first_wind != 0 or first_damage != 0:
        raise CurveError(
            f"curve {curve.curve_id} must start at (0, 0); "
            f"got ({first_wind}, {first_damage})"
        )

    previous_wind, previous_damage = curve.points[0]
    for wind, damage in curve.points[1:]:
        if not 0.0 <= damage <= 1.0:
            raise CurveError(
                f"curve {curve.curve_id} has damage fraction {damage} at {wind} mph, "
                "outside [0, 1]"
            )
        if wind <= previous_wind:
            raise CurveError(
                f"curve {curve.curve_id} wind must increase; {wind} follows {previous_wind}"
            )
        if damage < previous_damage:
            raise CurveError(
                f"curve {curve.curve_id} damage must not decrease; {damage} at {wind} mph "
                f"follows {previous_damage} at {previous_wind} mph"
            )
        previous_wind, previous_damage = wind, damage


@lru_cache
def load_curve_set(path: str | None = None) -> dict:
    """Read, validate and index the curve fixture. Cached; call `reset_caches` in tests.

    Returns the whole file rather than just the curves so callers can publish the
    provenance block: an assumed curve with its provenance is a usable prototype
    input, and the same curve without it is a liability.
    """
    curve_path = Path(path) if path else FIXTURES / "damage_curves.json"
    raw = json.loads(curve_path.read_text(encoding="utf-8"))

    curves: dict[tuple[str, str], Curve] = {}
    for entry in raw["curves"]:
        curve = Curve(
            curve_id=entry["curve_id"],
            vulnerability_class=entry["vulnerability_class"],
            upgrade_id=entry["upgrade_id"],
            wind_metric=entry["wind_metric"],
            points=tuple((float(w), float(d)) for w, d in entry["points"]),
            evidence_status=entry["evidence_status"],
            source_note=entry["source_note"],
        )
        validate_curve(curve)
        key = (curve.vulnerability_class, curve.upgrade_id)
        if key in curves:
            raise CurveError(
                f"two curves for class {key[0]} upgrade {key[1]}: "
                f"{curves[key].curve_id} and {curve.curve_id}"
            )
        curves[key] = curve

    return {
        "curve_set_id": raw["curve_set_id"],
        "wind_metric": raw["wind_metric"],
        "evidence_status": raw["evidence_status"],
        "provenance": raw["provenance"],
        "curves": curves,
    }


def _curve_assumption(curve_set: dict, evidence_statuses: set[str]) -> str:
    """One line on what the damage curves rest on, for the run's assumptions list."""
    if "assumed" in evidence_statuses:
        return (
            "Every damage curve in this run is an assumed fixture. The Finance workbook's "
            "upgrade damage-effect request is still unanswered."
        )
    return (
        f"Damage curves ({curve_set['curve_set_id']}) are published FEMA Hazus building "
        "loss functions mapped to the platform's classes; the mapping, including an "
        "assumed equal mix of gable and hip roofs, is in metadata.curve_provenance. Not "
        "checked against this portfolio's own claims, and the Finance workbook's upgrade "
        "damage-effect request is still unanswered."
    )


def reset_caches() -> None:
    load_curve_set.cache_clear()
    load_policy_template.cache_clear()


def find_curve(curves: dict[tuple[str, str], Curve], vulnerability_class: str, upgrade_id: str) -> Curve:
    curve = curves.get((vulnerability_class, upgrade_id))
    if curve is None:
        available = sorted(u for c, u in curves if c == vulnerability_class)
        raise MissingDataError(
            f"no {upgrade_id} curve for vulnerability class {vulnerability_class}. "
            f"Available upgrades for that class: {', '.join(available) or 'none'}"
        )
    return curve


def eligible_upgrades(curves: dict[tuple[str, str], Curve], vulnerability_class: str) -> list[str]:
    """Upgrades that have a curve for this class, so cannot be offered without one.

    post_fbc_2002 has no roof_straps curve because roof-to-wall connections are
    already code-required there, which is a modeling decision recorded in the fixture,
    not an omission to paper over.
    """
    return sorted(u for c, u in curves if c == vulnerability_class and u != BASELINE)


# --------------------------------------------------------------------------- #
# Calculation - the five rules from the brief
# --------------------------------------------------------------------------- #


def damage_fraction(curve: Curve, wind_mph: float, wind_metric: str | None = None) -> float:
    """Linearly interpolate the damage fraction at `wind_mph`.

    Above the curve's last point this raises rather than extrapolating. A curve that
    stops at 220 mph says nothing about 260 mph, and the convex tail means a silent
    extrapolation would not be a small error.
    """
    if wind_metric is not None and wind_metric != curve.wind_metric:
        raise UnitMismatchError(
            f"wind metric {wind_metric!r} does not match curve {curve.curve_id} "
            f"defined on {curve.wind_metric!r}. Agree the gust definition with the "
            "simulator owner rather than converting here."
        )
    if wind_mph < 0:
        raise EngineError(f"negative wind speed {wind_mph} mph")
    if wind_mph > curve.max_supported_wind:
        raise UnsupportedInputError(
            f"{wind_mph} mph is above the supported range of curve {curve.curve_id} "
            f"({curve.max_supported_wind} mph). Extend the curve rather than "
            "extrapolating it."
        )

    previous_wind, previous_damage = curve.points[0]
    for wind, damage in curve.points[1:]:
        if wind_mph <= wind:
            span = wind - previous_wind
            if span == 0:
                return damage
            weight = (wind_mph - previous_wind) / span
            return previous_damage + weight * (damage - previous_damage)
        previous_wind, previous_damage = wind, damage

    return curve.points[-1][1]


def damage_usd(replacement_cost_usd: float, fraction: float) -> float:
    """Physical damage. Valued on replacement cost, kept separate from coverage."""
    if replacement_cost_usd < 0:
        raise EngineError(f"negative replacement cost {replacement_cost_usd}")
    return replacement_cost_usd * fraction


def payout_usd(damage: float, deductible_usd: float, coverage_limit_usd: float) -> float:
    """min(limit, max(0, damage - deductible)).

    This is the prototype's agreed contract, not a general reading of an insurance
    policy. Finance must approve its scope before any number here is shown as a claim
    estimate.
    """
    if deductible_usd < 0:
        raise EngineError(f"negative deductible {deductible_usd}")
    if coverage_limit_usd < 0:
        raise EngineError(f"negative coverage limit {coverage_limit_usd}")
    return min(coverage_limit_usd, max(0.0, damage - deductible_usd))


def deductible_from_percent(percent: float, base_usd: float) -> float:
    """Convert a percentage deductible using its documented policy base.

    Florida hurricane deductibles are written as a percentage of Coverage A, so the
    base is the dwelling limit - not the damage. Applying the percentage to damage
    would understate the deductible on every loss smaller than the dwelling limit.
    """
    if not 0 <= percent <= 1:
        raise EngineError(f"deductible percent {percent} is not a fraction between 0 and 1")
    if base_usd <= 0:
        raise EngineError(f"deductible base {base_usd} must be positive")
    return percent * base_usd


@lru_cache
def load_policy_template(path: str | None = None) -> dict:
    template_path = Path(path) if path else FIXTURES / "policy_template.json"
    return json.loads(template_path.read_text(encoding="utf-8"))


def policy_from_template(property_id: str, coverage_a_usd: float, template: dict | None = None) -> Policy:
    """Build a Policy for one property from the illustrative Finance template.

    Coverage A scales with the individual home rather than being pinned to the
    workbook's $400,000 example, so a $1.2M property is not modeled as if it carried a
    $400,000 limit. The deductible percentage and its base travel with the policy so
    the arithmetic can be audited.
    """
    template = template or load_policy_template()
    percent = template["deductible"]["percent"]
    return Policy(
        property_id=property_id,
        deductible_usd=deductible_from_percent(percent, coverage_a_usd),
        coverage_limit_usd=coverage_a_usd,
        deductible_basis=template["deductible"]["basis"],
        deductible_percent=percent,
        deductible_base_usd=coverage_a_usd,
    )


# --------------------------------------------------------------------------- #
# Money formatting - the only place rounding happens
# --------------------------------------------------------------------------- #


def _money(value: float) -> float:
    """Round to cents on the way out.

    Every calculation above runs at full float precision and this is the single exit
    point, which is what keeps 279904.99999999994 from reaching a consumer. Amounts
    stay floats rather than becoming ints for whole dollars, so the type of a field
    never depends on its value.
    """
    rounded = round(value, 2)
    return 0.0 if rounded == 0 else rounded  # never -0.0


# --------------------------------------------------------------------------- #
# Batch: every storm x property x eligible upgrade
# --------------------------------------------------------------------------- #


def _index_exposures(exposures: list[WindExposure]) -> dict[tuple[str, str], WindExposure]:
    indexed: dict[tuple[str, str], WindExposure] = {}
    for exposure in exposures:
        key = (exposure.storm_id, exposure.property_id)
        if key in indexed:
            raise DuplicateRowError(
                f"two wind exposure rows for storm {key[0]} property {key[1]}"
            )
        indexed[key] = exposure
    return indexed


def compute_losses(
    properties: list[Property],
    policies: list[Policy],
    exposures: list[WindExposure],
    storm_ids: list[str],
    *,
    run_id: str,
    catalog_id: str,
    sampling_description: str,
    eligible_options: dict[str, list[str]] | None = None,
    curve_set: dict | None = None,
    wind_model_metadata: dict | None = None,
    extra_assumptions: list[str] | None = None,
) -> dict:
    """Price every storm against every property under every eligible upgrade.

    Returns the version 1.1 envelope Developer 2 consumes. Completeness is the point:
    every declared storm/property/upgrade combination gets a row, including zero-loss
    rows for homes a storm missed, and every declared id is published so the consumer
    can check nothing was dropped.
    """
    curve_set = curve_set or load_curve_set()
    curves = curve_set["curves"]

    properties_by_id = {p.property_id: p for p in properties}
    if len(properties_by_id) != len(properties):
        raise DuplicateRowError("the same property_id appears twice in properties")

    policies_by_id = {p.property_id: p for p in policies}
    if len(policies_by_id) != len(policies):
        raise DuplicateRowError("the same property_id appears twice in policies")

    indexed_exposures = _index_exposures(exposures)

    warnings: list[str] = []
    evidence_statuses = {curve.evidence_status for curve in curves.values()}
    rows: list[dict] = []
    resolved_options: list[dict] = []

    for prop in properties:
        if prop.property_id not in policies_by_id:
            raise MissingDataError(f"no policy for property {prop.property_id}")

        options = (
            eligible_options.get(prop.property_id)
            if eligible_options is not None
            else eligible_upgrades(curves, prop.vulnerability_class)
        )
        if options is None:
            options = eligible_upgrades(curves, prop.vulnerability_class)
        if BASELINE in options:
            raise EngineError(
                f"property {prop.property_id}: {BASELINE!r} is the current home, not an "
                "upgrade option"
            )
        resolved_options.append({"property_id": prop.property_id, "upgrade_ids": list(options)})
        if not options:
            warnings.append(
                f"property {prop.property_id} has no eligible upgrade for vulnerability "
                f"class {prop.vulnerability_class}; only baseline loss is reported"
            )

    for storm_id in storm_ids:
        for prop in properties:
            exposure = indexed_exposures.get((storm_id, prop.property_id))
            if exposure is None:
                raise MissingDataError(
                    f"no wind exposure for storm {storm_id} property {prop.property_id}. "
                    "A missing row means missing data, not zero loss: supply an explicit "
                    "zero-exposure row."
                )

            policy = policies_by_id[prop.property_id]
            baseline_curve = find_curve(curves, prop.vulnerability_class, BASELINE)

            # Computed once per storm/property so it is identical across upgrade rows.
            baseline_fraction = damage_fraction(
                baseline_curve, exposure.peak_gust_mph, exposure.wind_metric
            )
            baseline_damage = damage_usd(prop.replacement_cost_usd, baseline_fraction)
            baseline_payout = payout_usd(
                baseline_damage, policy.deductible_usd, policy.coverage_limit_usd
            )

            options = next(
                o["upgrade_ids"] for o in resolved_options if o["property_id"] == prop.property_id
            )

            for upgrade_id in options:
                upgrade_curve = find_curve(curves, prop.vulnerability_class, upgrade_id)
                upgraded_fraction = damage_fraction(
                    upgrade_curve, exposure.peak_gust_mph, exposure.wind_metric
                )
                upgraded_damage = damage_usd(prop.replacement_cost_usd, upgraded_fraction)
                upgraded_payout = payout_usd(
                    upgraded_damage, policy.deductible_usd, policy.coverage_limit_usd
                )
                avoided = baseline_payout - upgraded_payout

                # Preserved, never clamped. A negative value means the curves say the
                # upgrade made things worse, which is a data problem worth seeing.
                if avoided < 0:
                    warnings.append(
                        f"negative avoided payout for storm {storm_id} property "
                        f"{prop.property_id} upgrade {upgrade_id}: upgraded payout "
                        f"exceeds baseline. Review curves {baseline_curve.curve_id} and "
                        f"{upgrade_curve.curve_id}."
                    )

                rows.append(
                    {
                        "storm_id": storm_id,
                        "property_id": prop.property_id,
                        "upgrade_id": upgrade_id,
                        "peak_gust_mph": round(exposure.peak_gust_mph, 1),
                        "baseline_damage_usd": _money(baseline_damage),
                        "upgraded_damage_usd": _money(upgraded_damage),
                        "baseline_payout_usd": _money(baseline_payout),
                        "upgraded_payout_usd": _money(upgraded_payout),
                        "avoided_payout_usd": _money(avoided),
                    }
                )

    assumptions = [
        "Illustrative gross insurer payouts before reinsurance. Not calibrated and not "
        "validated against loss experience.",
        _curve_assumption(curve_set, evidence_statuses),
        "payout = min(coverage_limit, max(0, damage - deductible)). The prototype's "
        "agreed contract, pending Finance approval of its scope.",
        "Each storm is priced independently: the deductible resets and the building is "
        "assumed fully repaired beforehand. No annual accumulation, and no annual event "
        "rate is applied here.",
        "Replacement cost is taken as the property's insured value; no square-footage "
        "rebuild cost was available.",
        "Contents, loss of use, flood and storm surge, premium changes, reinsurance and "
        "claim expenses are out of scope.",
    ]
    if extra_assumptions:
        assumptions.extend(extra_assumptions)

    metadata = {
        "result_basis": RESULT_BASIS,
        "curve_set_id": curve_set["curve_set_id"],
        "curve_ids": sorted({c.curve_id for c in curves.values()}),
        "curve_wind_metric": curve_set["wind_metric"],
        "curve_provenance": curve_set["provenance"],
        "curve_source_notes": {c.curve_id: c.source_note for c in curves.values()},
        "sampling_description": sampling_description,
        "policy_basis": {
            "deductible_basis": policies[0].deductible_basis if policies else None,
            "deductible_percent": policies[0].deductible_percent if policies else None,
            "coverage_limit_basis": "coverage_a",
            "note": "A percentage deductible is applied to Coverage A, never to damage.",
        },
    }
    if wind_model_metadata:
        metadata["wind_model"] = wind_model_metadata

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "catalog_id": catalog_id,
        "storm_ids": list(storm_ids),
        "property_ids": [p.property_id for p in properties],
        "eligible_options": resolved_options,
        # 'assumed' if any material input is assumed. It describes provenance, not
        # whether the model has been validated.
        "evidence_status": "assumed" if "assumed" in evidence_statuses else "sourced",
        "rows": rows,
        "warnings": warnings,
        "assumptions": assumptions,
        "metadata": metadata,
    }
