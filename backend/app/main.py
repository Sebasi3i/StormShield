"""FastAPI application: settings, CORS, and every endpoint.

This one is the web layer, `schemas.py` is the contract, `risk.py` is the county risk
model and mitigation economics, `claims.py` is the damage and payout engine, and
`wind.py` is the provisional wind field that feeds it. Nothing here does arithmetic —
routes resolve counties, call into those modules, and shape responses.

All endpoints are stateless. Portfolios are never persisted: the client holds its
selection and sends it on each call, which keeps sessions, auth and portfolio tables
out of the MVP entirely.
"""

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import claims, risk, storms, wind
from .schemas import (
    CountyConcentration,
    CountyDetail,
    CountySummary,
    EligibleOption,
    HurricaneCategoryInfo,
    MitigationRequest,
    ModelMeta,
    PortfolioAnalysis,
    PortfolioMitigation,
    PortfolioRequest,
    PropertyInput,
    PropertyMitigation,
    PropertyRisk,
    ScenarioRequest,
    SimulationImpact,
    SimulationRequest,
    SimulationResult,
    StormLossPropertyInput,
    StormLossRequest,
    StormLossResponse,
)

API_PREFIX = "/api"

# Vite's dev server. Add deployed origins rather than widening to "*", which would
# also disable credentialed requests.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "WRP_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Load the 55,524-observation HURDAT2 record at boot, not on the first button press.

    A failure here is logged and swallowed. Every endpoint except storm generation works
    without the simulator, so a missing vendored tree should degrade one feature rather
    than stop the API from serving; `/api/v1/simulate-storm` reports it properly as a 503
    when it is actually called.
    """
    try:
        storms.historical_data()
    except Exception as error:  # noqa: BLE001 - startup must never be fatal
        print(f"[startup] hurricane simulator unavailable: {error}")
    yield


app = FastAPI(
    title="Weather Risk API",
    description=(
        "Severe weather risk intelligence for Florida property insurance. Score a "
        "property's exposure, simulate a hurricane of any category against a "
        "portfolio, and appraise whether funding mitigation upgrades beats paying "
        "the claims."
    ),
    version=risk.MODEL_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _resolve(
    properties: list[PropertyInput],
) -> tuple[list[tuple[PropertyInput, risk.County]], list[int]]:
    """Pair each property with its county, collecting ids that could not be placed.

    Reported rather than dropped: a portfolio that quietly analyzed 9 of 10
    selections would understate exposure with nothing on screen to say so.
    """
    resolved: list[tuple[PropertyInput, risk.County]] = []
    unresolved: list[int] = []

    for prop in properties:
        county, _ = risk.resolve_county(prop.latitude, prop.longitude, prop.county)
        if county is None:
            unresolved.append(prop.id)
        else:
            resolved.append((prop, county))

    if not resolved:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "No selected properties could be placed in a Florida county. "
                "This MVP covers Florida only.",
                "unresolvedIds": unresolved,
            },
        )
    return resolved, unresolved


def _property_risk(prop: PropertyInput, county: risk.County) -> PropertyRisk:
    breakdown = risk.county_breakdown(county)
    _, source = risk.resolve_county(prop.latitude, prop.longitude, prop.county)
    return PropertyRisk(
        id=prop.id,
        address=prop.address,
        city=prop.city,
        latitude=prop.latitude,
        longitude=prop.longitude,
        value=prop.value,
        county_fips=county.fips,
        county_name=county.name,
        county_source=source,
        risk=breakdown,
        estimated_annual_loss_usd=risk.estimated_annual_exposure(
            prop.value, breakdown["overall_risk"]
        ),
    )


def _weighted_centroid(resolved: list[tuple[PropertyInput, risk.County]]) -> tuple[float, float]:
    """Insured-value-weighted centre of a portfolio.

    Used as the default landfall point so the simulation defaults to a direct hit on
    wherever the money is, which is the case an underwriter actually needs to see.
    """
    total = sum(p.value for p, _ in resolved) or 1
    lat = sum(p.latitude * p.value for p, _ in resolved) / total
    lon = sum(p.longitude * p.value for p, _ in resolved) / total
    return lat, lon


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #


@app.get("/", tags=["meta"])
def root() -> dict:
    return {"message": "Weather Risk API is running", "docs": "/docs", "version": risk.MODEL_VERSION}


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Liveness, plus enough detail to tell a real build from a placeholder one."""
    return {
        "status": "healthy",
        "modelVersion": risk.MODEL_VERSION,
        "dataSource": risk.data_source(),
        "countiesWithRealData": risk.counties_with_real_data(),
        "countiesLoaded": len(risk.FL_COUNTIES),
        "measuresLoaded": len(risk.MEASURES),
    }


@app.get(f"{API_PREFIX}/meta/model", response_model=ModelMeta, tags=["meta"])
def model_meta() -> ModelMeta:
    """Publish weights, assumptions and provenance so a number can be audited."""
    return ModelMeta(
        model_version=risk.MODEL_VERSION,
        data_source=risk.data_source(),
        counties_with_real_data=risk.counties_with_real_data(),
        counties_covered=len(risk.FL_COUNTIES),
        weights=risk.WEIGHTS,
        hazard_labels=risk.HAZARD_LABELS,
        frequency_share=risk.FREQUENCY_SHARE,
        damage_share=risk.DAMAGE_SHARE,
        years_covered=risk.YEARS_COVERED,
        categories_available=[c.category for c in risk.CATEGORIES],
        measures=[
            {
                "id": m.id,
                "name": m.name,
                "fixedCost": m.fixed_cost,
                "valueRate": m.value_rate,
                "effectiveness": m.effectiveness,
                "lifespanYears": m.lifespan_years,
                "statuteCreditEligible": m.statute_credit,
            }
            for m in risk.MEASURES
        ],
        default_horizon_years=risk.DEFAULT_HORIZON_YEARS,
        default_discount_rate=risk.DEFAULT_DISCOUNT_RATE,
        warnings=risk.model_warnings(),
        sources=[
            "NOAA NCEI Storm Events Database",
            "NOAA HURDAT2 Atlantic hurricane tracks",
            "FEMA OpenFEMA disaster declarations and NFIP claims",
            "US Census ACS 5-year county characteristics",
            "Florida Statute 627.0629 wind mitigation credits",
        ],
    )


# --------------------------------------------------------------------------- #
# Counties
# --------------------------------------------------------------------------- #


@app.get(f"{API_PREFIX}/counties", response_model=list[CountySummary], tags=["counties"])
def list_counties() -> list[CountySummary]:
    """All 67 Florida counties with their score — one fetch for the choropleth."""
    out = []
    for county in risk.FL_COUNTIES:
        breakdown = risk.county_breakdown(county)
        out.append(
            CountySummary(
                fips=county.fips,
                name=county.name,
                centroid_lat=county.lat,
                centroid_lon=county.lon,
                coastal=county.coastal,
                overall_risk=breakdown["overall_risk"],
                risk_level=breakdown["risk_level"],
            )
        )
    return out


@app.get(f"{API_PREFIX}/counties/{{fips}}", response_model=CountyDetail, tags=["counties"])
def get_county(fips: str) -> CountyDetail:
    county = risk.county_by_fips(fips)
    if county is None:
        raise HTTPException(
            status_code=404,
            detail=f"No Florida county with FIPS {fips}. Expected 12001-12133.",
        )
    breakdown = risk.county_breakdown(county)
    return CountyDetail(
        fips=county.fips,
        name=county.name,
        centroid_lat=county.lat,
        centroid_lon=county.lon,
        coastal=county.coastal,
        overall_risk=breakdown["overall_risk"],
        risk_level=breakdown["risk_level"],
        risk=breakdown,
    )


# --------------------------------------------------------------------------- #
# Portfolio risk
# --------------------------------------------------------------------------- #


@app.post(
    f"{API_PREFIX}/portfolio/analyze", response_model=PortfolioAnalysis, tags=["portfolio"]
)
def analyze_portfolio(request: PortfolioRequest) -> PortfolioAnalysis:
    resolved, unresolved = _resolve(request.properties)
    risks = [_property_risk(prop, county) for prop, county in resolved]

    total_value = sum(r.value for r in risks)
    weighted = (
        round(sum(r.risk.overall_risk * r.value for r in risks) / total_value)
        if total_value
        else 0
    )

    by_county: dict[str, dict] = {}
    for r in risks:
        entry = by_county.setdefault(
            r.county_fips,
            {
                "county_fips": r.county_fips,
                "county_name": r.county_name,
                "property_count": 0,
                "insured_value_usd": 0,
                "overall_risk": r.risk.overall_risk,
            },
        )
        entry["property_count"] += 1
        entry["insured_value_usd"] += r.value

    concentration = sorted(by_county.values(), key=lambda e: e["insured_value_usd"], reverse=True)
    for entry in concentration:
        entry["share_of_portfolio"] = (
            round(entry["insured_value_usd"] / total_value, 4) if total_value else 0.0
        )

    worst = max(risks, key=lambda r: r.risk.overall_risk)
    top = concentration[0]

    return PortfolioAnalysis(
        property_count=len(risks),
        total_insured_value_usd=total_value,
        weighted_risk_score=weighted,
        risk_level=risk.risk_level(weighted),
        estimated_annual_loss_usd=sum(r.estimated_annual_loss_usd for r in risks),
        counties_represented=len(concentration),
        concentration=[CountyConcentration(**e) for e in concentration],
        highest_risk_property_id=worst.id,
        narrative=(
            f"{len(risks)} properties totalling ${total_value:,} insured value across "
            f"{len(concentration)} Florida counties. Value-weighted risk is "
            f"{weighted}/100 ({risk.risk_level(weighted)}). Largest concentration is "
            f"{top['county_name']} County at {top['share_of_portfolio'] * 100:.0f}% of "
            f"insured value, scoring {top['overall_risk']}/100."
        ),
        properties=risks,
        unresolved=unresolved,
        model_version=risk.MODEL_VERSION,
        data_source=risk.data_source(),
    )


# --------------------------------------------------------------------------- #
# Hurricane simulation
# --------------------------------------------------------------------------- #


@app.get(
    f"{API_PREFIX}/hurricane-categories",
    response_model=list[HurricaneCategoryInfo],
    tags=["simulation"],
)
def list_categories() -> list[HurricaneCategoryInfo]:
    """The five Saffir-Simpson categories available to simulate."""
    return [HurricaneCategoryInfo(**c._asdict()) for c in risk.CATEGORIES]


@app.post(f"{API_PREFIX}/simulate", response_model=SimulationResult, tags=["simulation"])
def simulate(request: SimulationRequest) -> SimulationResult:
    """Place a synthetic storm of the chosen category and price the damage.

    When `applyRecommendedMitigation` is on, each property is also priced as if its
    recommended retrofit package were already in place, so the response carries the
    investment case for this specific storm: loss avoided against what it cost.
    """
    category = risk.CATEGORIES_BY_NUMBER.get(request.category)
    if category is None:
        raise HTTPException(
            status_code=404, detail=f"Category {request.category} is not 1-5."
        )

    resolved, unresolved = _resolve(request.properties)

    if request.landfall_lat is not None and request.landfall_lon is not None:
        landfall_lat, landfall_lon = request.landfall_lat, request.landfall_lon
        landfall_source = "requested"
    else:
        landfall_lat, landfall_lon = _weighted_centroid(resolved)
        landfall_source = "portfolio-centroid"

    decay = request.storm_size_km or risk.DEFAULT_WIND_DECAY_KM

    impacts: list[SimulationImpact] = []
    total_value = total_loss = total_mitigated = total_mitigation_cost = 0
    affected = 0
    worst_id: int | None = None
    worst_loss = -1

    for prop, county in resolved:
        distance = risk.haversine_km(prop.latitude, prop.longitude, landfall_lat, landfall_lon)
        wind = risk.wind_at_distance(category, distance, decay)
        ratio = risk.wind_damage_ratio(wind)
        loss = round(prop.value * ratio)

        total_value += prop.value
        total_loss += loss
        if loss > 0:
            affected += 1
        if loss > worst_loss:
            worst_loss, worst_id = loss, prop.id

        impact = SimulationImpact(
            property_id=prop.id,
            address=prop.address,
            county_name=county.name,
            distance_to_landfall_km=round(distance, 1),
            modeled_wind_kt=wind,
            damage_ratio=round(ratio, 4),
            modeled_loss_usd=loss,
            severity=risk.severity(ratio),
        )

        if request.apply_recommended_mitigation:
            appraisal = risk.appraise_property(county, prop.value)
            measures = [
                m
                for m in appraisal["recommended_measure_ids"]
                if m not in prop.existing_measures
            ]
            applied = measures + prop.existing_measures
            cost = sum(
                risk.measure_cost(risk.MEASURES_BY_ID[m], prop.value) for m in measures
            )
            mitigated_ratio = ratio * risk.residual_factor(applied, "hurricane")
            mitigated_loss = round(prop.value * mitigated_ratio)

            impact.mitigated_damage_ratio = round(mitigated_ratio, 4)
            impact.mitigated_loss_usd = mitigated_loss
            impact.loss_avoided_usd = loss - mitigated_loss
            impact.mitigation_cost_usd = cost

            total_mitigated += mitigated_loss
            total_mitigation_cost += cost

        impacts.append(impact)

    impacts.sort(key=lambda i: i.modeled_loss_usd, reverse=True)
    loss_ratio = round(total_loss / total_value, 4) if total_value else 0.0

    narrative = (
        f"A {category.label} storm ({category.representative_wind_kt} kt) making "
        f"landfall at {landfall_lat:.2f}, {landfall_lon:.2f} models "
        f"${total_loss:,} of loss on ${total_value:,} insured value "
        f"({loss_ratio * 100:.1f}%), with {affected} of {len(impacts)} properties damaged."
    )

    result = SimulationResult(
        category=HurricaneCategoryInfo(**category._asdict()),
        landfall_lat=round(landfall_lat, 4),
        landfall_lon=round(landfall_lon, 4),
        landfall_source=landfall_source,
        storm_size_km=decay,
        property_count=len(impacts),
        total_insured_value_usd=total_value,
        modeled_portfolio_loss_usd=total_loss,
        portfolio_loss_ratio=loss_ratio,
        properties_affected=affected,
        worst_hit_property_id=worst_id,
        narrative=narrative,
        impacts=impacts,
        method=risk.SIMULATION_METHOD,
        model_version=risk.MODEL_VERSION,
        data_source=risk.data_source(),
    )

    if request.apply_recommended_mitigation:
        avoided = total_loss - total_mitigated
        result.mitigated_portfolio_loss_usd = total_mitigated
        result.loss_avoided_usd = avoided
        result.mitigation_cost_usd = total_mitigation_cost
        result.net_benefit_this_storm_usd = avoided - total_mitigation_cost
        result.narrative += (
            f" With the recommended retrofits in place the loss falls to "
            f"${total_mitigated:,}, avoiding ${avoided:,} for "
            f"${total_mitigation_cost:,} of upfront work — a net "
            f"${avoided - total_mitigation_cost:,} on this single storm."
        )

    return result


# --------------------------------------------------------------------------- #
# Mitigation appraisal
# --------------------------------------------------------------------------- #


@app.post(
    f"{API_PREFIX}/portfolio/mitigation",
    response_model=PortfolioMitigation,
    tags=["mitigation"],
)
def appraise_portfolio(request: MitigationRequest) -> PortfolioMitigation:
    """Should the insurer fund upgrades on these properties, and which ones first?

    This is the product's actual output. Everything else feeds it.
    """
    resolved, unresolved = _resolve(request.properties)

    appraisals: list[PropertyMitigation] = []
    baseline = residual = package_cost = 0
    total_npv = 0
    worth_investing = 0

    for prop, county in resolved:
        appraisal = risk.appraise_property(
            county, prop.value, request.horizon_years, request.discount_rate
        )
        # Measures already on the property are not recommended again.
        if prop.existing_measures:
            appraisal["recommended_measure_ids"] = [
                m
                for m in appraisal["recommended_measure_ids"]
                if m not in prop.existing_measures
            ]

        breakdown = risk.county_breakdown(county)
        appraisals.append(
            PropertyMitigation(
                property_id=prop.id,
                address=prop.address,
                county_name=county.name,
                value=prop.value,
                risk_score=breakdown["overall_risk"],
                **appraisal,
            )
        )

        baseline += appraisal["baseline_annual_loss_usd"]
        residual += appraisal["residual_annual_loss_usd"]
        package_cost += appraisal["package_cost_usd"]
        total_npv += appraisal["package_npv_usd"]
        if appraisal["recommended_measure_ids"]:
            worth_investing += 1

    appraisals.sort(key=lambda a: a.package_roi, reverse=True)

    annual_avoided = baseline - residual
    payback = package_cost / annual_avoided if annual_avoided > 0 else None
    total_value = sum(p.value for p, _ in resolved)

    if package_cost == 0:
        verdict = "Do not invest"
    elif payback is not None and payback <= request.horizon_years / 2:
        verdict = "Invest now"
    else:
        verdict = "Invest selectively"

    narrative = (
        f"Across {len(appraisals)} properties worth ${total_value:,}, expected annual "
        f"loss is ${baseline:,}. Funding the recommended retrofits costs "
        f"${package_cost:,} upfront and cuts that to ${residual:,} — "
        f"${annual_avoided:,} avoided each year, a "
        f"{100 * annual_avoided / baseline if baseline else 0:.0f}% reduction"
    )
    if payback is not None:
        narrative += (
            f", paying back in {payback:.1f} years against a "
            f"{request.horizon_years}-year horizon. Net present value "
            f"${total_npv:,}."
        )
    else:
        narrative += "."

    return PortfolioMitigation(
        property_count=len(appraisals),
        total_insured_value_usd=total_value,
        baseline_annual_loss_usd=baseline,
        residual_annual_loss_usd=residual,
        annual_avoided_loss_usd=annual_avoided,
        loss_reduction_pct=round(100 * annual_avoided / baseline, 1) if baseline else 0.0,
        total_package_cost_usd=package_cost,
        total_npv_usd=total_npv,
        portfolio_payback_years=round(payback, 1) if payback is not None else None,
        portfolio_roi=round(total_npv / package_cost, 3) if package_cost else 0.0,
        properties_worth_investing=worth_investing,
        horizon_years=request.horizon_years,
        discount_rate=request.discount_rate,
        verdict=verdict,
        narrative=narrative,
        priority_order=[a.property_id for a in appraisals],
        properties=appraisals,
        unresolved=unresolved,
        assumptions=risk.MITIGATION_ASSUMPTIONS,
        model_version=risk.MODEL_VERSION,
        data_source=risk.data_source(),
    )


# --------------------------------------------------------------------------- #
# Storm losses - damage and insurer payout, shared contract version 1.1
#
# Separate from /api/simulate above, and deliberately not a replacement for it.
# /api/simulate places a synthetic storm of a chosen CATEGORY at a chosen point and
# prices it against insured value: the underwriter's what-if. These endpoints price
# INDIVIDUAL storms from the simulator's catalog through supplied vulnerability curves
# and a policy deductible, and report insurer payout rather than economic loss.
# --------------------------------------------------------------------------- #

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _example_portfolio() -> dict:
    return json.loads((FIXTURES / "example_portfolio.json").read_text(encoding="utf-8"))


def _generated_run_id() -> str:
    return "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _engine_inputs(
    properties: list[StormLossPropertyInput],
) -> tuple[list[claims.Property], list[claims.Policy]]:
    """Turn request properties into engine properties and their policies.

    Coverage A defaults to replacement cost, and is also the base a percentage
    deductible is taken against - never the damage amount.
    """
    engine_properties = []
    policies = []

    for prop in properties:
        engine_properties.append(
            claims.Property(
                property_id=prop.property_id,
                replacement_cost_usd=prop.replacement_cost_usd,
                vulnerability_class=prop.vulnerability_class,
            )
        )
        coverage_a = prop.coverage_a_usd or prop.replacement_cost_usd
        if prop.deductible_usd is not None:
            policies.append(
                claims.Policy(
                    property_id=prop.property_id,
                    deductible_usd=prop.deductible_usd,
                    coverage_limit_usd=prop.coverage_limit_usd or coverage_a,
                )
            )
        else:
            policy = claims.policy_from_template(prop.property_id, coverage_a)
            if prop.coverage_limit_usd:
                policy = policy._replace(coverage_limit_usd=prop.coverage_limit_usd)
            policies.append(policy)

    return engine_properties, policies


def _require_coordinates(properties: list[StormLossPropertyInput]) -> None:
    uncoordinated = [
        prop.property_id
        for prop in properties
        if prop.latitude is None or prop.longitude is None
    ]
    if uncoordinated:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Every property needs latitude and longitude so the wind "
                "field can derive a gust from the storm track.",
                "propertyIds": uncoordinated,
            },
        )


def _storm_losses(
    properties: list[StormLossPropertyInput],
    storm_ids: list[str] | None,
    run_id: str | None,
    wind_exposures: list | None,
    eligible_options: list[EligibleOption] | None,
) -> dict:
    """Assemble engine inputs, run the engine, attach provenance.

    The only place in the web layer that knows how a request maps onto the engine.
    Engine rejections become 422s carrying the engine's message verbatim, because a
    message naming the storm, the property and the missing curve is what the caller
    needs in order to fix the call.
    """
    catalog = wind.load_catalog()
    ids = list(storm_ids) if storm_ids else list(catalog["storm_ids"])

    unknown = [storm_id for storm_id in ids if wind.storm_by_id(storm_id, catalog) is None]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Unknown storm ids for this catalog.",
                "unknownStormIds": unknown,
                "catalogId": catalog["catalog_id"],
                "available": catalog["storm_ids"],
            },
        )

    engine_properties, policies = _engine_inputs(properties)

    exposure_detail: list[dict] | None = None
    if wind_exposures:
        exposures = [
            claims.WindExposure(
                storm_id=exposure.storm_id,
                property_id=exposure.property_id,
                peak_gust_mph=exposure.peak_gust_mph,
                wind_metric=exposure.wind_metric,
            )
            for exposure in wind_exposures
        ]
        wind_metadata = {
            "source": "supplied by the caller, not derived from a track",
            "evidence_status": "caller-declared",
            "note": "Rows were used verbatim. The engine still rejects any wind metric "
            "that does not match the metric the curves are defined on.",
        }
    else:
        _require_coordinates(properties)
        coordinates = [
            (prop.property_id, prop.latitude, prop.longitude) for prop in properties
        ]
        exposures = []
        exposure_detail = []
        for storm_id in ids:
            storm = wind.storm_by_id(storm_id, catalog)
            storm_exposures, detail = wind.exposures_for_storm(storm, coordinates)
            exposures.extend(storm_exposures)
            exposure_detail.extend(detail)
        wind_metadata = wind.metadata()

    options = (
        {option.property_id: option.upgrade_ids for option in eligible_options}
        if eligible_options
        else None
    )

    try:
        result = claims.compute_losses(
            engine_properties,
            policies,
            exposures,
            ids,
            run_id=run_id or _generated_run_id(),
            catalog_id=catalog["catalog_id"],
            sampling_description=catalog["sampling_description"],
            eligible_options=options,
            wind_model_metadata=wind_metadata,
            extra_assumptions=[catalog["completeness_warning"]],
        )
    except claims.EngineError as error:
        raise HTTPException(
            status_code=422,
            detail={"message": str(error), "error": type(error).__name__},
        ) from error

    result["metadata"]["storm_catalog"] = {
        "catalog_id": catalog["catalog_id"],
        "imported_at": catalog["imported_at"],
        "source_wind_metric": catalog["wind_metric"],
        "source_wind_metric_note": catalog["wind_metric_note"],
        "completeness_warning": catalog["completeness_warning"],
    }
    if exposure_detail is not None:
        # Closest approach per property, so a zero-loss row reads as an audited miss
        # rather than looking like a row that went missing.
        result["metadata"]["wind_exposure_detail"] = exposure_detail
    return result


@app.get(f"{API_PREFIX}/v1/storm-catalog", tags=["storm losses"])
def storm_catalog() -> dict:
    """The individual storms available to price, with their tracks.

    Everything a client needs to draw and animate a storm: ordered track points with
    centre position, intensity and category. Wind here is sustained wind at the CENTRE
    in knots and not wind at a property, so the wind model block travels with it.
    """
    catalog = wind.load_catalog()
    return {**catalog, "wind_model": wind.metadata()}


@app.get(f"{API_PREFIX}/v1/damage-curves", tags=["storm losses"])
def damage_curves() -> dict:
    """The vulnerability curves and the policy template, with their provenance.

    Published so a client can show what a number rests on. Every curve here is an
    assumed fixture, and the evidence status and source note say so per curve rather
    than in a footnote somewhere else.
    """
    curve_set = claims.load_curve_set()
    classes = sorted({c.vulnerability_class for c in curve_set["curves"].values()})
    return {
        "curve_set_id": curve_set["curve_set_id"],
        "wind_metric": curve_set["wind_metric"],
        "evidence_status": curve_set["evidence_status"],
        "provenance": curve_set["provenance"],
        "curves": [
            {
                "curve_id": curve.curve_id,
                "vulnerability_class": curve.vulnerability_class,
                "upgrade_id": curve.upgrade_id,
                "wind_metric": curve.wind_metric,
                "evidence_status": curve.evidence_status,
                "source_note": curve.source_note,
                "points": [list(point) for point in curve.points],
            }
            for curve in curve_set["curves"].values()
        ],
        "vulnerability_classes": classes,
        "eligible_upgrades_by_class": {
            name: claims.eligible_upgrades(curve_set["curves"], name) for name in classes
        },
        "policy_template": claims.load_policy_template(),
        "wind_model": wind.metadata(),
    }


@app.get(
    f"{API_PREFIX}/v1/storm-losses/example",
    response_model=StormLossResponse,
    tags=["storm losses"],
)
def storm_losses_example(storm_id: str = "SYN0155") -> dict:
    """A complete worked run with no request body, to build a client against.

    Defaults to the Category 4 Miami landfall, which is the demonstration case worth
    having: it destroys value in Miami-Dade and Broward, and passes far enough from
    Jacksonville that the property there comes back as an explicit zero-loss row rather
    than as no row at all.
    """
    portfolio = _example_portfolio()
    properties = [
        StormLossPropertyInput(
            property_id=entry["property_id"],
            replacement_cost_usd=entry["replacement_cost_usd"],
            vulnerability_class=entry["vulnerability_class"],
            latitude=entry["latitude"],
            longitude=entry["longitude"],
        )
        for entry in portfolio["properties"]
    ]
    result = _storm_losses(properties, [storm_id], f"example-{storm_id}", None, None)
    result["metadata"]["example_portfolio"] = {
        "portfolio_id": portfolio["portfolio_id"],
        "label": portfolio["label"],
        "missing_input": portfolio["missing_input"],
        "replacement_cost_note": portfolio["replacement_cost_note"],
        "properties": portfolio["properties"],
    }
    return result


@app.post(
    f"{API_PREFIX}/v1/storm-losses",
    response_model=StormLossResponse,
    tags=["storm losses"],
)
def storm_losses(request: StormLossRequest) -> dict:
    """Price storms against properties, before and after each eligible upgrade.

    Returns every storm/property/upgrade combination, including zero-loss rows for
    homes a storm missed, because a missing row means missing data and not no loss.
    Send windExposures to price real property-level gusts; omit them and the
    provisional wind field derives gusts from the stored track.
    """
    return _storm_losses(
        request.properties,
        request.storm_ids,
        request.run_id,
        request.wind_exposures,
        request.eligible_options,
    )




# --------------------------------------------------------------------------- #
# Scenario generation - a live hurricane for a portfolio
# --------------------------------------------------------------------------- #


@app.get(f"{API_PREFIX}/v1/simulator", tags=["storm losses"])
def simulator() -> dict:
    """What generates the storms, how a Florida strike is defined, and what it is not."""
    try:
        return storms.simulator_info()
    except storms.SimulatorUnavailable as error:
        raise HTTPException(status_code=503, detail={"message": str(error)}) from error


@app.post(
    f"{API_PREFIX}/v1/simulate-storm",
    response_model=StormLossResponse,
    tags=["storm losses"],
)
def simulate_storm(request: ScenarioRequest) -> dict:
    """Generate a hurricane that hits Florida and price it against this portfolio.

    The demo path. Send the properties the user selected; get back a storm, its track for
    animation, and what it costs per property before and after each upgrade.

    The storm comes from the vendored simulator, unmodified: a Monte Carlo draw over the
    real HURDAT2 record. Because the real Atlantic mostly misses Florida, the generator
    keeps drawing until one storm reaches the requested category and actually strikes the
    state - about one draw in fifty, so roughly a second. How many draws it took is
    reported in `metadata.storm_generation`, and that count is the rarity of what you are
    looking at.

    The storm is NOT aimed at the portfolio. A Panhandle hurricane is a real Florida
    strike that legitimately does nothing to a Miami portfolio, and about 40% of runs will
    show little or no loss for that reason. That is the hazard being honest, not a failure.

    Read `metadata.wind_model` before quoting any figure. Turning the simulator's
    storm-centre wind into a gust at a building is still a placeholder owned by the
    simulator's author, and it is the single largest source of uncertainty here.
    """
    _require_coordinates(request.properties)
    engine_properties, policies = _engine_inputs(request.properties)

    try:
        storm, generation = storms.generate_florida_storm(
            min_category=request.min_category, seed=request.seed
        )
    except storms.SimulatorUnavailable as error:
        raise HTTPException(status_code=503, detail={"message": str(error)}) from error
    except storms.NoQualifyingStorm as error:
        raise HTTPException(status_code=504, detail={"message": str(error)}) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"message": str(error)}) from error

    coordinates = [
        (prop.property_id, prop.latitude, prop.longitude) for prop in request.properties
    ]
    exposures, exposure_detail = wind.exposures_for_storm(storm, coordinates)

    options = (
        {option.property_id: option.upgrade_ids for option in request.eligible_options}
        if request.eligible_options
        else None
    )

    try:
        result = claims.compute_losses(
            engine_properties,
            policies,
            exposures,
            [storm["storm_id"]],
            run_id=request.run_id or _generated_run_id(),
            catalog_id=f"live-{storm['storm_id']}",
            sampling_description=storms.simulator_info()["method"],
            eligible_options=options,
            wind_model_metadata=wind.metadata(),
            extra_assumptions=[
                storms.simulator_info()["not_a_rate"],
                generation["interpretation"],
            ],
        )
    except claims.EngineError as error:
        raise HTTPException(
            status_code=422,
            detail={"message": str(error), "error": type(error).__name__},
        ) from error

    result["metadata"]["storm_generation"] = generation
    result["metadata"]["simulator"] = storms.simulator_info()
    # The whole storm, track included, so a client can draw what it just priced without
    # a second request.
    result["metadata"]["storm"] = storm
    result["metadata"]["wind_exposure_detail"] = exposure_detail
    result["metadata"]["worst_gust_on_portfolio_mph"] = max(
        (round(exposure.peak_gust_mph, 1) for exposure in exposures), default=0.0
    )
    return result
