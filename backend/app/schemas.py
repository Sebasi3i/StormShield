"""The API contract.

Request models mirror the frontend's `Property` type exactly (id, address, city,
county, latitude, longitude, value) so the map can POST its selection with no
transformation. Responses use camelCase.

If the frontend's Property type changes, this file changes with it and nothing else
does.

One exception, at the bottom of this file: the storm-loss models of shared contract
version 1.1 stay snake_case, because those field names are fixed by a written contract
with the developer consuming them.
"""

from datetime import date
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


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


# --------------------------------------------------------------------------- #
# Storm losses - shared response contract version 1.1
#
# These models deliberately do NOT inherit from ApiModel. Every other response in this
# file is camelCase, but version 1.1 is a written contract shared with the developer
# consuming it, and its field names are snake_case. Renaming them to match house style
# would break the other side of the contract, so here house style loses.
# --------------------------------------------------------------------------- #


EvidenceStatus = Literal["assumed", "sourced"]

# Vulnerability classes and upgrade ids are plain strings on purpose. What exists is
# decided by the curve fixture, not by this file: a Literal here would mean the API
# rejects a class the research team just supplied a curve for.


class StormLossPropertyInput(BaseModel):
    """One building. Accepts the frontend Property shape without transformation.

    The aliases let the map POST what it already holds - id and value - while the
    engine works in the terms the brief defines: a string property id and a building
    replacement cost kept separate from the coverage limit.
    """

    model_config = ConfigDict(populate_by_name=True)

    property_id: str = Field(
        validation_alias=AliasChoices("property_id", "propertyId", "id"),
        description="String id. An integer from the frontend list is accepted and coerced.",
    )
    replacement_cost_usd: float = Field(
        gt=0,
        validation_alias=AliasChoices("replacement_cost_usd", "replacementCostUsd", "value"),
        description="Building replacement cost. Insured value is accepted as a stand-in.",
    )
    vulnerability_class: str = Field(
        default="pre_fbc_2002",
        description="Must have a baseline curve in the curve set, or the row is rejected.",
    )
    roof_shape: str = Field(
        default="unknown",
        description="'gable' or 'hip' selects the matching Hazus curve. Anything else, "
        "including the default 'unknown', gets the blended (equal gable/hip mix) curve.",
    )
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    coverage_a_usd: float | None = Field(
        default=None,
        gt=0,
        description="Dwelling limit. Defaults to replacement cost, which also sets the "
        "percentage deductible base.",
    )
    deductible_usd: float | None = Field(
        default=None,
        ge=0,
        description="Overrides the template percentage deductible with a flat amount.",
    )
    coverage_limit_usd: float | None = Field(default=None, gt=0)

    @field_validator("property_id", mode="before")
    @classmethod
    def _coerce_id(cls, value: object) -> object:
        # The frontend numbers its properties; the contract calls for strings.
        return str(value) if isinstance(value, int) else value


class WindExposureInput(BaseModel):
    """Property-level peak gust, supplied rather than computed from a track.

    wind_metric is required, not defaulted. The whole point of carrying it is that a
    mismatch against the curve gets rejected instead of silently converted, and a
    default would quietly assert the very thing that needs agreeing.
    """

    storm_id: str
    property_id: str
    peak_gust_mph: float = Field(ge=0)
    wind_metric: str


class EligibleOption(BaseModel):
    property_id: str
    upgrade_ids: list[str]


class RoofShapeSelection(BaseModel):
    """Which curve a property actually used, auditable independently of the curve_ids
    the run publishes for the whole portfolio in metadata.

    fallback_reason is null when the declared shape was recognised and this class/
    upgrade had a curve for it; otherwise it names why the blended curve was used - an
    unrecognised declared value (also raised as a warning, since that is a caller-input
    problem) or simply a curve set with no shape-specific curve for this class/upgrade
    (a fact about the curve set, not the input).
    """

    property_id: str
    declared_roof_shape: str
    resolved_roof_shape: str
    baseline_curve_id: str
    upgrade_curve_ids: dict[str, str]
    fallback_reason: list[str] | None


class StormTrackPointInput(BaseModel):
    """One six-hourly fix of a storm supplied with a request, in the catalog's shape."""

    model_config = ConfigDict(extra="allow")

    step: int = Field(ge=0)
    timestamp: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    max_wind_kt: float = Field(ge=0, le=250)


class StormInput(BaseModel):
    """A storm supplied with a request rather than taken from the stored catalog.

    The shape the catalog and POST /api/v1/storms/generate return; any other fields
    (landfall, member seed and so on) are carried through untouched.
    """

    model_config = ConfigDict(extra="allow")

    storm_id: str = Field(min_length=1, max_length=64)
    track: list[StormTrackPointInput] = Field(min_length=1, max_length=400)


class StormLossRequest(BaseModel):
    """Price storms against properties.

    Wind reaches the engine one of two ways. Supply wind_exposures and they are used
    verbatim, which is the path for real exposures from the simulator owner. Omit them
    and latitude/longitude are required, so the wind field in app/wind.py (backed by
    the wind_field package) derives the gust from the stored storm track.
    """

    properties: list[StormLossPropertyInput] = Field(min_length=1, max_length=500)
    storm_ids: list[str] | None = Field(
        default=None, description="Defaults to every storm in the catalog."
    )
    run_id: str | None = None
    wind_exposures: list[WindExposureInput] | None = None
    storms: list[StormInput] | None = Field(
        default=None,
        max_length=20,
        description="Price these storms instead of the stored catalog, for example ones "
        "from POST /api/v1/storms/generate. storm_ids then defaults to all of them.",
    )
    eligible_options: list[EligibleOption] | None = Field(
        default=None,
        description="Per property. Omitted, every upgrade with a curve for the class is "
        "priced. Baseline is not an option.",
    )


class StormLossRow(BaseModel):
    """One storm, one property, one upgrade.

    Baseline figures repeat across the upgrade rows of the same storm and property by
    design: they are computed once, so a consumer can check they match.
    """

    storm_id: str
    property_id: str
    upgrade_id: str
    peak_gust_mph: float
    baseline_damage_usd: float
    upgraded_damage_usd: float
    baseline_payout_usd: float
    upgraded_payout_usd: float
    avoided_payout_usd: float = Field(
        description="baseline_payout - upgraded_payout. Negative values are preserved "
        "and flagged in warnings, never clamped to zero."
    )


class StormLossResponse(BaseModel):
    """The version 1.1 envelope.

    Completeness is checkable by construction: storm_ids, property_ids and
    eligible_options declare what should be present, so a missing row is detectable
    rather than indistinguishable from a zero loss.
    """

    schema_version: str
    run_id: str
    catalog_id: str
    storm_ids: list[str]
    property_ids: list[str]
    eligible_options: list[EligibleOption]
    roof_shape_selection: list[RoofShapeSelection] = Field(
        description="Per property: declared and resolved roof_shape, the actual "
        "baseline/upgrade curve_ids used, and why (if at all) it fell back to blended."
    )
    evidence_status: EvidenceStatus = Field(
        description="assumed if any material input is assumed. Describes provenance, "
        "not whether the model has been validated."
    )
    rows: list[StormLossRow]
    warnings: list[str]
    assumptions: list[str]
    metadata: dict = Field(
        description="Curve ids and source notes, gust definition, reference height and "
        "terrain convention, sampling description, policy basis."
    )


class GenerateStormsRequest(BaseModel):
    """Generate an ensemble of storms from one starting point with the hurricane simulator.

    Every storm starts at the same place, wind and date; only its seed differs, so the
    ensemble shows the spread of paths from one starting condition. The same request
    always returns the same storms. snake_case, like the storm-loss contract it feeds.
    """

    latitude: float = Field(ge=-90, le=90, description="Where the storm forms.")
    longitude: float = Field(ge=-180, le=180, description="Where the storm forms.")
    max_wind_kt: float = Field(
        default=45.0, ge=20, le=185, description="Sustained centre wind at the start, knots."
    )
    start_date: date | None = Field(
        default=None,
        description="When the storm forms. Defaults to 10 September of the current year, "
        "close to the peak of the Atlantic season.",
    )
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    count: int = Field(default=5, ge=1, le=10, description="How many storms to generate.")


class GenerateFloridaStormsRequest(BaseModel):
    """Generate a batch of storms from a random starting point that reaches Florida.

    The API draws a starting point at random from historical Atlantic genesis positions
    (Cape Verde, the Caribbean, the Bahamas, the Gulf and so on), runs `count` storms
    from it, and keeps the first starting point whose batch puts at least
    `min_florida_hits` storms over Florida at Category 3 or stronger. Everything follows
    from `seed`: the same request in the same season year returns the same batch.
    """

    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    count: int = Field(default=10, ge=1, le=10, description="How many storms in the batch.")
    max_wind_kt: float = Field(
        default=70.0,
        ge=20,
        le=185,
        description="Sustained centre wind given to every storm at its start, knots. "
        "Weaker starts rarely reach Florida as major hurricanes, so the search takes longer.",
    )
    min_florida_hits: int = Field(
        default=2,
        ge=1,
        le=10,
        description="How many of the batch must be over Florida at Category 3 or stronger.",
    )
    season_year: int | None = Field(
        default=None,
        ge=1900,
        le=2100,
        description="Calendar year for the storms' timestamps. Defaults to the current year.",
    )

    @model_validator(mode="after")
    def _hits_within_count(self) -> "GenerateFloridaStormsRequest":
        if self.min_florida_hits > self.count:
            raise ValueError("min_florida_hits cannot exceed count.")
        return self


# --------------------------------------------------------------------------- #
# Sample insurer - premium credits for mitigation, schema insurer-demo-v1
# --------------------------------------------------------------------------- #


class InsurerProgramInput(BaseModel):
    """Funding and program assumptions. Every default is the specification's demo
    assumption, not a workbook input."""

    grant_share: float = Field(default=0.25, ge=0, le=1, description="Share of each project's effective cost the insurer grants.")
    grant_cap_usd: float = Field(default=2500, ge=0)
    inspection_usd_per_project: float = Field(default=100, ge=0)
    fixed_setup_usd: float = Field(default=500, ge=0, description="Charged once per program, only if at least one project proceeds.")
    annual_admin_usd: float = Field(default=0, ge=0)
    budget_usd: float = Field(default=20000, ge=0, description="Upfront insurer budget: grants, inspections and setup.")
    horizon_years: int = Field(default=10, ge=1, le=30)
    discount_rate: float = Field(default=0.05, ge=0, le=1)


class EventOnlyAnnualModel(BaseModel):
    kind: Literal["event_only"] = "event_only"


class OneEventOrNoneAnnualModel(BaseModel):
    """At most one catalog event per year, with an explicit, invented probability."""

    kind: Literal["one_event_or_none"]
    annual_event_probability: float = Field(ge=0, le=1)
    conditional_storm_weights: dict[str, float] = Field(
        description="One nonnegative weight per storm being run; must name exactly those storms and sum to more than zero."
    )


class InsurerCompareRequest(BaseModel):
    """Compare the current book with the selected projects, homeowner-funded and
    insurer co-funded, on the requested catalog storms."""

    preset_id: str | None = Field(default=None, description="Defaults to the fixture's default preset (app_consistent_demo).")
    policy_ids: list[str] | None = Field(default=None, description="Defaults to every policy in the book.")
    storm_ids: list[str] | None = Field(default=None, description="Catalog storm ids. Defaults to the whole stored catalog.")
    selected_proposal_ids: list[str] | None = Field(default=None, description="Defaults to every proposal on the chosen policies; [] selects none.")
    policy_plan_id: Literal["demo-deductible-5pct"] = "demo-deductible-5pct"
    deductible_fraction: float | None = Field(
        default=None, ge=0, le=1,
        description="Sensitivity: reprice every policy at this fraction of Coverage A with the premium held fixed.",
    )
    program: InsurerProgramInput = Field(default_factory=InsurerProgramInput)
    annual_model: EventOnlyAnnualModel | OneEventOrNoneAnnualModel = Field(
        default_factory=EventOnlyAnnualModel, discriminator="kind"
    )


class InsurerOptimizeRequest(InsurerCompareRequest):
    """The same request; requires the one_event_or_none annual model."""

    objective: Literal["insurer_npv"] = "insurer_npv"
