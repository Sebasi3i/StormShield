"""The API contract.

Request models mirror the frontend's `Property` type exactly (id, address, city,
county, latitude, longitude, value) so the map can POST its selection with no
transformation. Responses use camelCase.

If the frontend's Property type changes, this file changes with it and nothing else
does.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def to_camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(word.capitalize() for word in rest)


class ApiModel(BaseModel):
    """camelCase out, either case in."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


RiskLevel = Literal["Low", "Moderate", "Elevated", "High", "Severe"]
HazardKey = Literal["hurricane", "flood", "storm"]
Severity = Literal["None", "Minor", "Moderate", "Major", "Catastrophic"]
Verdict = Literal["Invest now", "Invest selectively", "Do not invest"]


# --------------------------------------------------------------------------- #
# Input - matches frontend/src/types/Property.ts
# --------------------------------------------------------------------------- #


class PropertyInput(ApiModel):
    id: int
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    value: int = Field(gt=0, description="Insured value in whole USD")
    address: str | None = None
    city: str | None = None
    county: str | None = Field(
        default=None,
        description="County name. Preferred over coordinates when it matches a "
        "Florida county, since a human-entered name beats centroid inference.",
    )
    existing_measures: list[str] = Field(
        default_factory=list,
        description="Mitigation measure ids already present on the property. These "
        "are excluded from recommendations and credited in simulations.",
    )


class PortfolioRequest(ApiModel):
    """Stateless: the client sends its whole selection on every call."""

    properties: list[PropertyInput] = Field(min_length=1, max_length=500)


# --------------------------------------------------------------------------- #
# Risk scoring
# --------------------------------------------------------------------------- #


class SubScore(ApiModel):
    """One hazard component, carrying enough context to explain itself.

    percentile, weight and contribution are here so the UI can show the arithmetic
    behind a score rather than asserting it. Contributions sum to overallRisk.
    """

    key: HazardKey
    label: str
    score: int = Field(ge=0, le=100)
    percentile: int = Field(ge=0, le=100, description="Rank among Florida's 67 counties")
    weight: float = Field(ge=0, le=1)
    contribution: float = Field(description="score * weight; these sum to overallRisk")
    drivers: list[str] = Field(default_factory=list)
    event_count: int
    damage_usd: int


class RiskBreakdown(ApiModel):
    overall_risk: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    sub_scores: list[SubScore] = Field(description="By contribution, descending")
    narrative: str
    total_event_count: int
    total_damage_usd: int
    years_covered: str
    model_version: str
    data_source: str = Field(description="fixture | partial | live")


# --------------------------------------------------------------------------- #
# Counties
# --------------------------------------------------------------------------- #


class CountySummary(ApiModel):
    """One row of the choropleth."""

    fips: str
    name: str
    centroid_lat: float
    centroid_lon: float
    coastal: bool
    overall_risk: int = Field(ge=0, le=100)
    risk_level: RiskLevel


class CountyDetail(CountySummary):
    risk: RiskBreakdown


# --------------------------------------------------------------------------- #
# Properties and portfolio
# --------------------------------------------------------------------------- #


class PropertyRisk(ApiModel):
    id: int
    address: str | None
    city: str | None
    latitude: float
    longitude: float
    value: int
    county_fips: str
    county_name: str
    county_source: Literal["provided", "coordinates"] = Field(
        description="Whether the county came from the request or was inferred"
    )
    risk: RiskBreakdown
    estimated_annual_loss_usd: int = Field(
        description="Indicative expected annual loss. Not an actuarial AAL."
    )


class CountyConcentration(ApiModel):
    county_fips: str
    county_name: str
    property_count: int
    insured_value_usd: int
    share_of_portfolio: float = Field(ge=0, le=1)
    overall_risk: int


class PortfolioAnalysis(ApiModel):
    property_count: int
    total_insured_value_usd: int
    weighted_risk_score: int = Field(
        ge=0, le=100, description="Insured-value-weighted, not a plain average"
    )
    risk_level: RiskLevel
    estimated_annual_loss_usd: int
    counties_represented: int
    concentration: list[CountyConcentration] = Field(description="By insured value, desc")
    highest_risk_property_id: int | None
    narrative: str
    properties: list[PropertyRisk]
    unresolved: list[int] = Field(
        default_factory=list,
        description="Ids of properties that could not be placed in a Florida county",
    )
    model_version: str
    data_source: str


# --------------------------------------------------------------------------- #
# Hurricane simulation - synthetic, by category
# --------------------------------------------------------------------------- #


class HurricaneCategoryInfo(ApiModel):
    category: int = Field(ge=1, le=5)
    label: str
    min_wind_kt: int
    max_wind_kt: int | None = Field(description="null for Category 5, which is open-ended")
    representative_wind_kt: int = Field(description="The wind speed the model uses")
    description: str


class SimulationRequest(ApiModel):
    """Place a synthetic storm of a chosen category and see what it costs.

    Landfall is optional: omitted, the storm is centred on the portfolio's
    value-weighted centroid, which is the direct-hit stress case rather than an
    arbitrary one.
    """

    category: int = Field(ge=1, le=5)
    properties: list[PropertyInput] = Field(min_length=1, max_length=500)
    landfall_lat: float | None = Field(default=None, ge=-90, le=90)
    landfall_lon: float | None = Field(default=None, ge=-180, le=180)
    storm_size_km: float | None = Field(
        default=None,
        gt=10,
        le=600,
        description="Wind decay length. Smaller is a compact intense storm, larger a "
        "broad one. Defaults to 150 km.",
    )
    apply_recommended_mitigation: bool = Field(
        default=True,
        description="Also compute the mitigated loss, using each property's "
        "recommended package, for the side-by-side comparison.",
    )


class SimulationImpact(ApiModel):
    property_id: int
    address: str | None
    county_name: str
    distance_to_landfall_km: float
    modeled_wind_kt: int
    damage_ratio: float = Field(ge=0, le=1)
    modeled_loss_usd: int
    severity: Severity
    # Populated when mitigation is applied.
    mitigated_damage_ratio: float | None = None
    mitigated_loss_usd: int | None = None
    loss_avoided_usd: int | None = None
    mitigation_cost_usd: int | None = None


class SimulationResult(ApiModel):
    category: HurricaneCategoryInfo
    landfall_lat: float
    landfall_lon: float
    landfall_source: Literal["requested", "portfolio-centroid"]
    storm_size_km: float
    property_count: int
    total_insured_value_usd: int
    modeled_portfolio_loss_usd: int
    portfolio_loss_ratio: float = Field(ge=0, le=1)
    properties_affected: int
    worst_hit_property_id: int | None
    # The investment case, in one storm.
    mitigated_portfolio_loss_usd: int | None = None
    loss_avoided_usd: int | None = None
    mitigation_cost_usd: int | None = None
    net_benefit_this_storm_usd: int | None = Field(
        default=None, description="Loss avoided minus what the upgrades cost"
    )
    narrative: str
    impacts: list[SimulationImpact] = Field(description="By modeled loss, descending")
    method: str
    model_version: str
    data_source: str


# --------------------------------------------------------------------------- #
# Mitigation appraisal - the core product
# --------------------------------------------------------------------------- #


class MeasureAppraisal(ApiModel):
    measure_id: str
    name: str
    description: str
    upfront_cost_usd: int
    annual_avoided_loss_usd: int = Field(description="Standalone, ignoring other measures")
    npv_avoided_loss_usd: int
    net_benefit_usd: int
    payback_years: float | None = Field(description="null when the measure avoids nothing")
    roi: float = Field(description="(NPV of avoided loss - cost) / cost")
    lifespan_years: int
    statute_credit_eligible: bool = Field(
        description="Recognised for Florida wind mitigation premium credit (FS 627.0629)"
    )
    hazards_addressed: list[str]
    marginal_annual_avoided_usd: int = Field(
        description="Avoided loss given the measures already selected ahead of it"
    )
    marginal_npv_usd: int
    in_recommended_package: bool


class MitigationRequest(ApiModel):
    properties: list[PropertyInput] = Field(min_length=1, max_length=500)
    horizon_years: int = Field(default=10, ge=1, le=40)
    discount_rate: float = Field(default=0.05, ge=0.0, le=0.30)


class PropertyMitigation(ApiModel):
    property_id: int
    address: str | None
    county_name: str
    value: int
    risk_score: int
    baseline_annual_loss_usd: int
    residual_annual_loss_usd: int
    annual_avoided_loss_usd: int
    loss_reduction_pct: float
    package_cost_usd: int
    package_npv_usd: int = Field(description="NPV of avoided loss, net of cost")
    package_payback_years: float | None
    package_roi: float
    recommended_measure_ids: list[str]
    verdict: Verdict
    rationale: str
    measures: list[MeasureAppraisal] = Field(description="All measures, by ROI descending")


class PortfolioMitigation(ApiModel):
    """The underwriting answer: how much to spend, and what it buys."""

    property_count: int
    total_insured_value_usd: int
    baseline_annual_loss_usd: int
    residual_annual_loss_usd: int
    annual_avoided_loss_usd: int
    loss_reduction_pct: float
    total_package_cost_usd: int
    total_npv_usd: int
    portfolio_payback_years: float | None
    portfolio_roi: float
    properties_worth_investing: int
    horizon_years: int
    discount_rate: float
    verdict: Verdict
    narrative: str
    # Best return first, so a capped budget goes to the right properties.
    priority_order: list[int] = Field(description="Property ids by package ROI, descending")
    properties: list[PropertyMitigation]
    unresolved: list[int] = Field(default_factory=list)
    assumptions: str
    model_version: str
    data_source: str


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #


class ModelMeta(ApiModel):
    model_version: str
    data_source: str
    counties_with_real_data: int
    counties_covered: int
    weights: dict[str, float]
    hazard_labels: dict[str, str]
    frequency_share: float
    damage_share: float
    years_covered: str
    categories_available: list[int]
    measures: list[dict] = Field(description="The full cost/effectiveness assumption table")
    default_horizon_years: int
    default_discount_rate: float
    warnings: list[str]
    sources: list[str]
