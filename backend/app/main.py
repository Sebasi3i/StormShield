"""FastAPI application: settings, CORS, and every endpoint.

Three files total. This one is the web layer, `schemas.py` is the contract, and
`risk.py` is the model. Nothing here does arithmetic — routes resolve counties, call
into `risk`, and shape responses.

All endpoints are stateless. Portfolios are never persisted: the client holds its
selection and sends it on each call, which keeps sessions, auth and portfolio tables
out of the MVP entirely.
"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import risk
from .schemas import (
    CountyConcentration,
    CountyDetail,
    CountySummary,
    HurricaneCategoryInfo,
    MitigationRequest,
    ModelMeta,
    PortfolioAnalysis,
    PortfolioMitigation,
    PortfolioRequest,
    PropertyInput,
    PropertyMitigation,
    PropertyRisk,
    SimulationImpact,
    SimulationRequest,
    SimulationResult,
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

app = FastAPI(
    title="Weather Risk API",
    description=(
        "Severe weather risk intelligence for Florida property insurance. Score a "
        "property's exposure, simulate a hurricane of any category against a "
        "portfolio, and appraise whether funding mitigation upgrades beats paying "
        "the claims."
    ),
    version=risk.MODEL_VERSION,
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
