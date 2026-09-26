"""Reference data, the risk model, hurricane simulation, and mitigation economics.

The product question is not "how risky is this property" but "should the insurer
fund upgrades now to avoid paying claims later". Risk scoring is an input to that
decision; the mitigation appraisal at the bottom of this file is the output.

Two sections to read before changing anything:
  - DATA DROP: where the real NOAA/FEMA metrics plug in (one file, no code changes).
  - MITIGATION ECONOMICS: the cost and effectiveness assumptions, all in one table.
"""

import csv
import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

MODEL_VERSION = "0.3.0"
YEARS_COVERED = "1996-2025"

# Provisional hazard weights. The numbers a judge is most likely to question, so
# they live in one place and are published on /api/meta/model.
WEIGHTS: dict[str, float] = {
    "hurricane": 0.45,
    "flood": 0.35,
    "storm": 0.20,
}

HAZARD_LABELS: dict[str, str] = {
    "hurricane": "Hurricane & Tropical Storm",
    "flood": "Flood & Storm Surge",
    "storm": "Severe Storm, Wind & Hail",
}

# Within each hazard, how much of the score comes from how often events happen
# versus how costly they were. Damage alone would rank counties by wealth; frequency
# alone would ignore severity.
FREQUENCY_SHARE = 0.5
DAMAGE_SHARE = 0.5

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"
if os.getenv("WRP_DATA_DIR"):
    DATA_DIR = Path(os.environ["WRP_DATA_DIR"])


# --------------------------------------------------------------------------- #
# Florida counties
# --------------------------------------------------------------------------- #
#
# FIPS codes and names are authoritative. Centroids are APPROXIMATE - fine for
# placing a marker, not computed from boundaries. Replace with real polygon
# centroids from the Census cartographic boundary files when those arrive.
#
# Trap: Dade County became Miami-Dade in 1997 and was recoded from FIPS 12025 to
# 12086. 12025 is retired but still appears in older NOAA and FEMA records, so any
# join against historical data must map it forward first. See RETIRED_FIPS.


class County(NamedTuple):
    fips: str
    name: str
    lat: float
    lon: float
    coastal: bool


RETIRED_FIPS = {"12025": "12086"}  # Dade -> Miami-Dade

FL_COUNTIES: tuple[County, ...] = (
    County("12001", "Alachua", 29.68, -82.35, False),
    County("12003", "Baker", 30.33, -82.28, False),
    County("12005", "Bay", 30.24, -85.63, True),
    County("12007", "Bradford", 29.95, -82.17, False),
    County("12009", "Brevard", 28.30, -80.70, True),
    County("12011", "Broward", 26.15, -80.45, True),
    County("12013", "Calhoun", 30.41, -85.20, False),
    County("12015", "Charlotte", 26.90, -81.94, True),
    County("12017", "Citrus", 28.85, -82.52, True),
    County("12019", "Clay", 29.98, -81.86, False),
    County("12021", "Collier", 26.10, -81.38, True),
    County("12023", "Columbia", 30.22, -82.62, False),
    County("12027", "DeSoto", 27.19, -81.81, False),
    County("12029", "Dixie", 29.58, -83.19, True),
    County("12031", "Duval", 30.34, -81.66, True),
    County("12033", "Escambia", 30.61, -87.34, True),
    County("12035", "Flagler", 29.47, -81.29, True),
    County("12037", "Franklin", 29.91, -84.85, True),
    County("12039", "Gadsden", 30.58, -84.61, False),
    County("12041", "Gilchrist", 29.72, -82.80, False),
    County("12043", "Glades", 26.95, -81.18, False),
    County("12045", "Gulf", 29.90, -85.27, True),
    County("12047", "Hamilton", 30.49, -82.95, False),
    County("12049", "Hardee", 27.49, -81.81, False),
    County("12051", "Hendry", 26.55, -81.17, False),
    County("12053", "Hernando", 28.55, -82.47, True),
    County("12055", "Highlands", 27.34, -81.34, False),
    County("12057", "Hillsborough", 27.91, -82.35, True),
    County("12059", "Holmes", 30.87, -85.81, False),
    County("12061", "Indian River", 27.70, -80.57, True),
    County("12063", "Jackson", 30.79, -85.21, False),
    County("12065", "Jefferson", 30.42, -83.89, True),
    County("12067", "Lafayette", 30.02, -83.18, False),
    County("12069", "Lake", 28.76, -81.71, False),
    County("12071", "Lee", 26.58, -81.87, True),
    County("12073", "Leon", 30.46, -84.28, False),
    County("12075", "Levy", 29.28, -82.80, True),
    County("12077", "Liberty", 30.24, -84.88, False),
    County("12079", "Madison", 30.44, -83.47, False),
    County("12081", "Manatee", 27.48, -82.35, True),
    County("12083", "Marion", 29.21, -82.06, False),
    County("12085", "Martin", 27.08, -80.40, True),
    County("12086", "Miami-Dade", 25.61, -80.50, True),
    County("12087", "Monroe", 25.00, -80.80, True),
    County("12089", "Nassau", 30.61, -81.76, True),
    County("12091", "Okaloosa", 30.66, -86.60, True),
    County("12093", "Okeechobee", 27.38, -80.89, False),
    County("12095", "Orange", 28.51, -81.32, False),
    County("12097", "Osceola", 28.06, -81.15, False),
    County("12099", "Palm Beach", 26.65, -80.44, True),
    County("12101", "Pasco", 28.31, -82.43, True),
    County("12103", "Pinellas", 27.88, -82.73, True),
    County("12105", "Polk", 27.95, -81.70, False),
    County("12107", "Putnam", 29.61, -81.74, False),
    County("12109", "St. Johns", 29.91, -81.41, True),
    County("12111", "St. Lucie", 27.38, -80.45, True),
    County("12113", "Santa Rosa", 30.71, -86.98, True),
    County("12115", "Sarasota", 27.18, -82.36, True),
    County("12117", "Seminole", 28.71, -81.22, False),
    County("12119", "Sumter", 28.70, -82.08, False),
    County("12121", "Suwannee", 30.19, -82.99, False),
    County("12123", "Taylor", 30.05, -83.60, True),
    County("12125", "Union", 30.05, -82.37, False),
    County("12127", "Volusia", 29.06, -81.15, True),
    County("12129", "Wakulla", 30.15, -84.38, True),
    County("12131", "Walton", 30.62, -86.17, True),
    County("12133", "Washington", 30.61, -85.67, False),
)

COUNTIES_BY_FIPS: dict[str, County] = {c.fips: c for c in FL_COUNTIES}

# Lowercased, tolerating "St."/"Saint" and a trailing "County", so a name typed by a
# human or copied from another dataset still matches.
COUNTIES_BY_NAME: dict[str, County] = {}
for _c in FL_COUNTIES:
    COUNTIES_BY_NAME[_c.name.lower()] = _c
    COUNTIES_BY_NAME[_c.name.lower().replace("st.", "saint")] = _c
    COUNTIES_BY_NAME[_c.name.lower().replace(".", "")] = _c

# Rough Florida bounding box, used to reject clicks in the Gulf, the Atlantic or a
# neighbouring state before bothering to assign a county.
FL_BOUNDS = {"min_lat": 24.3, "max_lat": 31.1, "min_lon": -87.7, "max_lon": -79.9}


# =========================================================================== #
# DATA DROP - the one place the real NOAA/FEMA data plugs in
# =========================================================================== #
#
# The model consumes per-county RAW METRICS, not scores. Drop a file at either:
#
#     data/processed/county_metrics.csv
#     data/processed/county_metrics.json
#
# and it is picked up on next server start. No code changes, no endpoint changes,
# no frontend changes. /health and /api/meta/model flip dataSource from "fixture"
# to "live" so you can tell at a glance which you are looking at.
#
# CSV format - one row per county, header required:
#
#   fips,hurricane_events,hurricane_damage_usd,flood_events,flood_damage_usd,storm_events,storm_damage_usd
#   12086,37,3480000000,44,1430000000,69,82800000
#
# JSON format - object keyed by 5-digit FIPS string:
#
#   {"12086": {"hurricane_events": 37, "hurricane_damage_usd": 3480000000, ...}}
#
# Rules for whoever builds the file:
#   - fips is the 5-digit string, zero-padded. Map retired 12025 to 12086 first.
#   - *_events are counts over YEARS_COVERED, already allocated to counties. For
#     NOAA rows with CZ_TYPE='Z', apportion across the zone's counties first - a
#     raw CZ_FIPS join silently misassigns most hurricane and coastal-flood events,
#     and it looks like it worked.
#   - *_damage_usd are whole dollars, CPI-adjusted to 2025. Parse NOAA's
#     DAMAGE_PROPERTY "2.10K"/"1.5M"/"12.00B" strings; treat blank as unknown, not
#     as zero.
#   - Counties missing from the file fall back to fixture values, so a partial drop
#     is safe. /health reports how many counties have real data.
#
# Anything beyond these six numbers - housing units, NFIP claim counts, flood zones
# - is a new field on CountyMetrics plus a new term in _score_tables(). Nothing
# outside this file changes.


class CountyMetrics(NamedTuple):
    """Raw per-county historical facts. The only data input the model needs."""

    hurricane_events: int
    hurricane_damage_usd: int
    flood_events: int
    flood_damage_usd: int
    storm_events: int
    storm_damage_usd: int


METRIC_FIELDS = CountyMetrics._fields


def _southness(lat: float) -> float:
    """0.0 at the Georgia line, 1.0 at Key West."""
    span = FL_BOUNDS["max_lat"] - FL_BOUNDS["min_lat"]
    return max(0.0, min(1.0, (FL_BOUNDS["max_lat"] - lat) / span))


def _fixture_metrics(county: County) -> CountyMetrics:
    """PLACEHOLDER metrics, shaped from geography.

    Deliberately generates plausible event counts and damage dollars rather than
    plausible scores, so the real normalization and percentile pipeline runs against
    them. When the real file lands only the numbers change, not the maths.
    """
    south = _southness(county.lat)
    coastal = county.coastal

    hurricane_events = round(6 + 26 * south + (8 if coastal else 0))
    hurricane_damage = round(
        hurricane_events * (18_000_000 + 55_000_000 * south) * (1.6 if coastal else 1.0)
    )

    flood_events = round(10 + 22 * south + (16 if coastal else 0))
    flood_damage = round(
        flood_events * (5_000_000 + 14_000_000 * south) * (2.0 if coastal else 1.0)
    )

    # Severe convective activity peaks over the central peninsula rather than at the
    # southern tip, so this is a ridge, not a gradient.
    centrality = 1 - min(1.0, abs(county.lat - 28.3) / 3.0)
    storm_events = round(60 + 90 * centrality)

    return CountyMetrics(
        hurricane_events=hurricane_events,
        hurricane_damage_usd=hurricane_damage,
        flood_events=flood_events,
        flood_damage_usd=flood_damage,
        storm_events=storm_events,
        storm_damage_usd=round(storm_events * 1_200_000),
    )


def _read_metrics_file() -> dict[str, dict]:
    """Read a dropped metrics file if one exists. Returns {} when none is present."""
    csv_path = DATA_DIR / "county_metrics.csv"
    json_path = DATA_DIR / "county_metrics.json"

    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        return {str(r["fips"]).strip().zfill(5): r for r in rows if r.get("fips")}

    if json_path.exists():
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        return {str(k).strip().zfill(5): v for k, v in raw.items()}

    return {}


@lru_cache
def load_metrics() -> tuple[dict[str, CountyMetrics], str, int]:
    """Metrics for all 67 counties, the data source, and how many came from file.

    A malformed row falls back to fixture rather than taking the API down, and the
    count in /health shows the shortfall. Failing loud on a bad cell would mean one
    typo in a teammate's export breaks the demo.
    """
    from_file = _read_metrics_file()
    metrics: dict[str, CountyMetrics] = {}
    real_count = 0

    for county in FL_COUNTIES:
        row = from_file.get(RETIRED_FIPS.get(county.fips, county.fips))
        if row:
            try:
                metrics[county.fips] = CountyMetrics(
                    **{f: int(float(row[f])) for f in METRIC_FIELDS}
                )
                real_count += 1
                continue
            except (KeyError, TypeError, ValueError):
                pass
        metrics[county.fips] = _fixture_metrics(county)

    if real_count == 0:
        source = "fixture"
    elif real_count < len(FL_COUNTIES):
        source = "partial"
    else:
        source = "live"
    return metrics, source, real_count


def data_source() -> str:
    return load_metrics()[1]


def counties_with_real_data() -> int:
    return load_metrics()[2]


def reset_caches() -> None:
    """Clear cached metrics and score tables so a newly dropped file is picked up."""
    load_metrics.cache_clear()
    _score_tables.cache_clear()


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def _percentile_ranks(values: dict[str, float]) -> dict[str, int]:
    """Percentile rank of each county within Florida, 1-100.

    Percentile rather than min-max on purpose. One multi-billion-dollar hurricane
    figure in a single county would flatten all 66 others toward zero under min-max,
    giving a map with one red county and no usable gradient. Rank is immune to that,
    and it reads better: "higher than 94% of Florida counties".
    """
    total = len(values)
    ranked = sorted(values.items(), key=lambda kv: kv[1])
    return {fips: round(100 * (i + 1) / total) for i, (fips, _) in enumerate(ranked)}


@lru_cache
def _score_tables() -> dict[str, dict[str, tuple[int, int]]]:
    """Per hazard, per county: (hazard score, frequency percentile)."""
    metrics = load_metrics()[0]
    tables: dict[str, dict[str, tuple[int, int]]] = {}

    for hazard in WEIGHTS:
        event_pct = _percentile_ranks(
            {f: float(getattr(m, f"{hazard}_events")) for f, m in metrics.items()}
        )
        damage_pct = _percentile_ranks(
            {f: float(getattr(m, f"{hazard}_damage_usd")) for f, m in metrics.items()}
        )
        tables[hazard] = {
            fips: (
                round(FREQUENCY_SHARE * event_pct[fips] + DAMAGE_SHARE * damage_pct[fips]),
                event_pct[fips],
            )
            for fips in metrics
        }
    return tables


def risk_level(score: float) -> str:
    if score >= 80:
        return "Severe"
    if score >= 65:
        return "High"
    if score >= 50:
        return "Elevated"
    if score >= 35:
        return "Moderate"
    return "Low"


def _drivers(hazard: str, county: County, percentile: int, damage: int) -> list[str]:
    drivers = [f"Ranks above {percentile}% of Florida counties on event frequency"]
    if damage:
        drivers.append(f"${damage / 1e9:.2f}B in recorded property damage, {YEARS_COVERED}")
    if hazard == "hurricane":
        drivers.append(
            "Coastal exposure to Atlantic and Gulf tracks"
            if county.coastal
            else "Inland position reduces peak wind exposure"
        )
        if county.lat < 27.5:
            drivers.append("Sits in Florida's most frequented landfall corridor")
    elif hazard == "flood":
        drivers.append(
            "Storm surge exposure along the coastline"
            if county.coastal
            else "Rainfall-driven flooding without direct surge exposure"
        )
    elif 26.0 <= county.lat <= 30.0:
        drivers.append("Central peninsula convective and tornado activity")
    return drivers


def county_breakdown(county: County) -> dict:
    """The full explainable breakdown for one county."""
    metrics = load_metrics()[0][county.fips]
    tables = _score_tables()

    sub_scores = []
    overall = 0.0
    total_events = 0
    total_damage = 0

    for hazard, weight in WEIGHTS.items():
        score, percentile = tables[hazard][county.fips]
        events = getattr(metrics, f"{hazard}_events")
        damage = getattr(metrics, f"{hazard}_damage_usd")
        contribution = round(score * weight, 1)

        overall += contribution
        total_events += events
        total_damage += damage

        sub_scores.append(
            {
                "key": hazard,
                "label": HAZARD_LABELS[hazard],
                "score": score,
                "percentile": percentile,
                "weight": weight,
                "contribution": contribution,
                "drivers": _drivers(hazard, county, percentile, damage),
                "event_count": events,
                "damage_usd": damage,
            }
        )

    overall_int = round(overall)
    sub_scores.sort(key=lambda s: s["contribution"], reverse=True)
    top, second = sub_scores[0], sub_scores[1]

    return {
        "overall_risk": overall_int,
        "risk_level": risk_level(overall_int),
        "sub_scores": sub_scores,
        "narrative": (
            f"{county.name} County scores {overall_int}/100 ({risk_level(overall_int)}), "
            f"driven primarily by {top['label'].lower()} ({top['score']}/100, "
            f"{top['contribution']} points of the total), followed by "
            f"{second['label'].lower()} ({second['score']}/100, "
            f"{second['contribution']} points). {total_events} recorded events and "
            f"${total_damage / 1e9:.2f}B in property damage over {YEARS_COVERED}."
        ),
        "total_event_count": total_events,
        "total_damage_usd": total_damage,
        "years_covered": YEARS_COVERED,
        "model_version": MODEL_VERSION,
        "data_source": data_source(),
    }


def annual_damage_ratio(overall_risk: int) -> float:
    """Indicative annual damage as a fraction of insured value.

    Convex in the score, because loss does not scale linearly with hazard. A
    placeholder calibration, NOT an actuarial average annual loss, and labelled that
    way everywhere it surfaces.
    """
    return (overall_risk / 100) ** 2 * 0.012


def estimated_annual_exposure(value: int, overall_risk: int) -> int:
    return round(value * annual_damage_ratio(overall_risk))


# --------------------------------------------------------------------------- #
# Geography
# --------------------------------------------------------------------------- #

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def in_florida_bounds(lat: float, lon: float) -> bool:
    return (
        FL_BOUNDS["min_lat"] <= lat <= FL_BOUNDS["max_lat"]
        and FL_BOUNDS["min_lon"] <= lon <= FL_BOUNDS["max_lon"]
    )


def county_by_name(name: str | None) -> County | None:
    if not name:
        return None
    key = name.strip().lower().removesuffix(" county").strip()
    return COUNTIES_BY_NAME.get(key) or COUNTIES_BY_NAME.get(key.replace(".", ""))


def county_by_fips(fips: str) -> County | None:
    return COUNTIES_BY_FIPS.get(RETIRED_FIPS.get(fips, fips))


def nearest_county(lat: float, lon: float) -> County | None:
    """Assign a coordinate to a county by nearest centroid.

    An approximation of point-in-polygon that will misassign points near a shared
    border, especially in the dense south-east. Replaced by real point-in-polygon
    against Census boundaries when those arrive - same signature, no caller changes.
    """
    if not in_florida_bounds(lat, lon):
        return None
    return min(FL_COUNTIES, key=lambda c: haversine_km(lat, lon, c.lat, c.lon))


def resolve_county(
    latitude: float, longitude: float, name: str | None = None
) -> tuple[County | None, str]:
    """Find a property's county, preferring a supplied name over its coordinates.

    A human-entered county name is more trustworthy than nearest-centroid inference,
    so it wins when it matches a real Florida county. Returns which source was used
    so the API can report the guess rather than hide it.
    """
    named = county_by_name(name)
    if named is not None:
        return named, "provided"
    return nearest_county(latitude, longitude), "coordinates"


# =========================================================================== #
# Hurricane simulation - synthetic storms by Saffir-Simpson category
# =========================================================================== #
#
# Deliberately NOT tied to named historical storms. The insurer's question is "what
# happens to this portfolio if a Cat 4 comes ashore here", which needs a category
# and a landfall point, not a replay of 1992. Categories and their wind ranges are
# the Saffir-Simpson scale; the representative wind is the value used in the model.


class HurricaneCategory(NamedTuple):
    category: int
    label: str
    min_wind_kt: int
    max_wind_kt: int | None
    representative_wind_kt: int
    description: str


CATEGORIES: tuple[HurricaneCategory, ...] = (
    HurricaneCategory(
        1, "Category 1", 64, 82, 70,
        "74-95 mph. Damage to roof covering, gutters, siding and trees. Power "
        "outages likely. Well-built structures survive largely intact.",
    ),
    HurricaneCategory(
        2, "Category 2", 83, 95, 89,
        "96-110 mph. Major roof and siding damage, shallow-rooted trees down, "
        "near-total power loss lasting days to weeks.",
    ),
    HurricaneCategory(
        3, "Category 3", 96, 112, 104,
        "111-129 mph. Devastating damage begins. Roof decking and gable ends fail "
        "on well-built homes. Water and power unavailable for days to weeks.",
    ),
    HurricaneCategory(
        4, "Category 4", 113, 136, 124,
        "130-156 mph. Catastrophic damage. Severe exterior wall and roof structure "
        "loss on well-built homes. Areas left uninhabitable for weeks to months.",
    ),
    HurricaneCategory(
        5, "Category 5", 137, None, 150,
        "157+ mph. Catastrophic damage. A high percentage of framed homes destroyed "
        "with total roof failure and wall collapse.",
    ),
)

CATEGORIES_BY_NUMBER: dict[int, HurricaneCategory] = {c.category: c for c in CATEGORIES}

# Wind falls off with distance from the centre. 150 km e-folding is a crude stand-in
# for a real wind field; adjustable per simulation so a compact intense storm can be
# contrasted with a broad one.
DEFAULT_WIND_DECAY_KM = 150.0

# Below this, wind does not meaningfully damage code-compliant structures.
DAMAGE_THRESHOLD_KT = 50.0

SIMULATION_METHOD = (
    "A synthetic storm of the selected Saffir-Simpson category is placed at the "
    "chosen landfall point. Wind at each property decays exponentially with "
    "distance from the centre and is converted to a damage ratio by a convex "
    "vulnerability curve above a 50 kt threshold. Placeholder calibration; storm "
    "surge and rainfall flooding are not yet modeled separately."
)


def wind_at_distance(
    category: HurricaneCategory, distance_km: float, decay_km: float = DEFAULT_WIND_DECAY_KM
) -> int:
    return round(category.representative_wind_kt * math.exp(-distance_km / decay_km))


def wind_damage_ratio(wind_kt: float) -> float:
    """Fraction of insured value lost at a given wind speed."""
    if wind_kt <= DAMAGE_THRESHOLD_KT:
        return 0.0
    excess = (wind_kt - DAMAGE_THRESHOLD_KT) / 100.0
    return min(0.85, 0.9 * excess**2.2)


def severity(damage_ratio: float) -> str:
    if damage_ratio <= 0:
        return "None"
    if damage_ratio < 0.05:
        return "Minor"
    if damage_ratio < 0.20:
        return "Moderate"
    if damage_ratio < 0.45:
        return "Major"
    return "Catastrophic"


# =========================================================================== #
# MITIGATION ECONOMICS - the actual product
# =========================================================================== #
#
# ASSUMPTIONS, NOT PUBLISHED CONSTANTS. Every cost and effectiveness figure below is
# an engineering estimate for planning purposes. They are gathered here, exposed on
# /api/meta/model, and reported with every recommendation so the sensitivity is
# visible instead of buried. Replace them with carrier loss experience or IBHS test
# data when that becomes available.
#
# Grounding: Florida Statute 627.0629 already requires insurers to give premium
# discounts for wind mitigation features, and the My Safe Florida Home program
# exists on the same premise - hardening a structure is cheaper than paying the
# claim. The measures below are the ones that statute and program recognise.

# A 10-year horizon is deliberately conservative: most of these measures last 20-30
# years, so a positive result over 10 understates the case rather than overstating it.
DEFAULT_HORIZON_YEARS = 10
DEFAULT_DISCOUNT_RATE = 0.05


class Measure(NamedTuple):
    """A mitigation retrofit.

    cost = fixed_cost + value_rate * property_value, which is how this work is
    actually priced: a base mobilisation plus scaling with the size of the structure,
    for which insured value is the available proxy.

    effectiveness maps hazard key -> fraction of THAT hazard's expected loss avoided.
    A measure absent from the mapping does nothing for that hazard.
    """

    id: str
    name: str
    description: str
    fixed_cost: int
    value_rate: float
    effectiveness: dict[str, float]
    lifespan_years: int
    statute_credit: bool  # Recognised for Florida wind mitigation premium credit


MEASURES: tuple[Measure, ...] = (
    Measure(
        "opening-protection",
        "Impact-Rated Windows & Doors",
        "Replace glazing and exterior doors with impact-rated assemblies, or fit "
        "tested shutters. The largest single wind mitigation credit under Florida "
        "statute, because breached openings cause pressurisation and then "
        "structural failure, not just water damage.",
        4_000, 0.030, {"hurricane": 0.25, "storm": 0.20}, 25, True,
    ),
    Measure(
        "roof-to-wall",
        "Roof-to-Wall Connections",
        "Retrofit hurricane straps or clips tying roof trusses to the wall "
        "structure. Addresses the failure mode that turns a damaged roof into a "
        "total loss.",
        2_200, 0.004, {"hurricane": 0.20, "storm": 0.10}, 30, True,
    ),
    Measure(
        "roof-deck",
        "Roof Deck Attachment",
        "Re-nail roof sheathing with 8d ring-shank nails at reduced spacing. "
        "Cheapest per point of loss avoided when done during a scheduled re-roof.",
        1_800, 0.003, {"hurricane": 0.15, "storm": 0.12}, 25, True,
    ),
    Measure(
        "secondary-water",
        "Secondary Water Resistance",
        "Sealed roof deck beneath the covering, so losing shingles does not mean "
        "water entering the building. Low cost, and it addresses the interior "
        "damage that drives claim severity.",
        1_200, 0.002, {"hurricane": 0.10, "storm": 0.08}, 25, True,
    ),
    Measure(
        "garage-bracing",
        "Garage Door Bracing",
        "Reinforce or replace the garage door to a wind-rated assembly. Often the "
        "largest unprotected opening on the building.",
        900, 0.001, {"hurricane": 0.08, "storm": 0.05}, 20, True,
    ),
    Measure(
        "flood-openings",
        "Flood Vents & Wet Floodproofing",
        "Engineered flood openings and flood-resistant materials below the design "
        "flood elevation, letting water pass through rather than collapsing walls.",
        3_500, 0.004, {"flood": 0.25}, 30, False,
    ),
    Measure(
        "elevation",
        "Structure Elevation",
        "Raise the lowest floor above base flood elevation. By far the most "
        "expensive measure and by far the most effective against flood - only "
        "justifiable where flood exposure dominates.",
        28_000, 0.045, {"flood": 0.65}, 40, False,
    ),
)

MEASURES_BY_ID: dict[str, Measure] = {m.id: m for m in MEASURES}

MITIGATION_ASSUMPTIONS = (
    "Costs are planning estimates of the form fixed + rate x insured value, not "
    "quotes. Effectiveness figures are engineering assumptions about the share of "
    "each hazard's expected loss avoided, combined multiplicatively across measures. "
    "Replace both with carrier loss experience or IBHS test data before relying on "
    "the output for underwriting."
)


def measure_cost(measure: Measure, property_value: int) -> int:
    return round(measure.fixed_cost + measure.value_rate * property_value)


def hazard_expected_loss(county: County, property_value: int) -> dict[str, float]:
    """Split a property's expected annual loss across hazards.

    Apportioned by each hazard's weighted contribution to the county's score, so a
    coastal county's loss is dominated by wind and surge while an inland one leans
    on convective storms. This is what makes the mitigation ranking differ by
    location instead of recommending the same measures everywhere.
    """
    breakdown = county_breakdown(county)
    total_eal = property_value * annual_damage_ratio(breakdown["overall_risk"])
    total_contribution = sum(s["contribution"] for s in breakdown["sub_scores"]) or 1.0
    return {
        s["key"]: total_eal * (s["contribution"] / total_contribution)
        for s in breakdown["sub_scores"]
    }


def residual_factor(measure_ids: list[str], hazard: str) -> float:
    """Fraction of a hazard's loss REMAINING after applying a set of measures.

    Combined multiplicatively, not additively. Three measures at 20% each leave
    0.8^3 = 51% of the loss, so they avoid 49% - not 60%. Summing effectiveness is
    how these models end up promising more than they deliver, and past four or five
    measures it would exceed 100% and imply negative loss.
    """
    residual = 1.0
    for measure_id in measure_ids:
        measure = MEASURES_BY_ID.get(measure_id)
        if measure:
            residual *= 1.0 - measure.effectiveness.get(hazard, 0.0)
    return residual


def npv_of_avoided_loss(annual_avoided: float, horizon_years: int, rate: float) -> float:
    """Present value of an annual saving over a horizon."""
    return sum(annual_avoided / ((1 + rate) ** y) for y in range(1, horizon_years + 1))


def appraise_measure(
    measure: Measure,
    county: County,
    property_value: int,
    horizon_years: int = DEFAULT_HORIZON_YEARS,
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
) -> dict:
    """Cost, avoided loss, payback and ROI for one measure on one property."""
    by_hazard = hazard_expected_loss(county, property_value)
    annual_avoided = sum(
        loss * measure.effectiveness.get(hazard, 0.0) for hazard, loss in by_hazard.items()
    )
    cost = measure_cost(measure, property_value)
    npv_avoided = npv_of_avoided_loss(annual_avoided, horizon_years, discount_rate)
    payback = cost / annual_avoided if annual_avoided > 0 else None

    return {
        "measure_id": measure.id,
        "name": measure.name,
        "description": measure.description,
        "upfront_cost_usd": cost,
        "annual_avoided_loss_usd": round(annual_avoided),
        "npv_avoided_loss_usd": round(npv_avoided),
        "net_benefit_usd": round(npv_avoided - cost),
        "payback_years": round(payback, 1) if payback is not None else None,
        "roi": round((npv_avoided - cost) / cost, 3) if cost else 0.0,
        "lifespan_years": measure.lifespan_years,
        "statute_credit_eligible": measure.statute_credit,
        "hazards_addressed": sorted(measure.effectiveness),
    }


def appraise_property(
    county: County,
    property_value: int,
    horizon_years: int = DEFAULT_HORIZON_YEARS,
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
) -> dict:
    """Rank every measure for one property, then build the recommended package.

    The package is not simply "every measure with positive NPV". Measures overlap:
    once opening protection has removed a quarter of wind loss, the next measure acts
    only on what is left. So candidates are considered in order of standalone return
    and each is re-tested against the loss that actually remains, which is what stops
    the package from counting the same avoided dollar twice.
    """
    by_hazard = hazard_expected_loss(county, property_value)
    baseline_annual = sum(by_hazard.values())

    appraisals = [
        appraise_measure(m, county, property_value, horizon_years, discount_rate)
        for m in MEASURES
    ]
    appraisals.sort(key=lambda a: a["roi"], reverse=True)

    selected: list[str] = []
    package_cost = 0

    for appraisal in appraisals:
        measure = MEASURES_BY_ID[appraisal["measure_id"]]
        marginal_annual = sum(
            by_hazard[hazard]
            * residual_factor(selected, hazard)
            * measure.effectiveness.get(hazard, 0.0)
            for hazard in by_hazard
        )
        marginal_npv = npv_of_avoided_loss(marginal_annual, horizon_years, discount_rate)
        cost = appraisal["upfront_cost_usd"]

        appraisal["marginal_annual_avoided_usd"] = round(marginal_annual)
        appraisal["marginal_npv_usd"] = round(marginal_npv)
        appraisal["in_recommended_package"] = marginal_npv > cost

        if marginal_npv > cost:
            selected.append(measure.id)
            package_cost += cost

    residual_annual = sum(
        by_hazard[h] * residual_factor(selected, h) for h in by_hazard
    )
    annual_avoided = baseline_annual - residual_annual
    npv_avoided = npv_of_avoided_loss(annual_avoided, horizon_years, discount_rate)
    payback = package_cost / annual_avoided if annual_avoided > 0 else None

    if not selected:
        verdict = "Do not invest"
        rationale = (
            "No measure returns more than it costs over the appraisal horizon at this "
            "property's exposure level. Revisit if exposure or costs change."
        )
    elif payback is not None and payback <= horizon_years / 2:
        verdict = "Invest now"
        rationale = (
            f"The recommended package costs ${package_cost:,} and avoids "
            f"${round(annual_avoided):,} of expected loss a year, paying back in "
            f"{payback:.1f} years against a {horizon_years}-year horizon."
        )
    else:
        verdict = "Invest selectively"
        rationale = (
            f"The package pays back in {payback:.1f} years against a {horizon_years}-year "
            f"horizon. Worth funding the highest-return measures; the marginal ones are "
            f"defensible but not compelling."
        )

    return {
        "baseline_annual_loss_usd": round(baseline_annual),
        "residual_annual_loss_usd": round(residual_annual),
        "annual_avoided_loss_usd": round(annual_avoided),
        "loss_reduction_pct": round(
            100 * annual_avoided / baseline_annual if baseline_annual else 0.0, 1
        ),
        "package_cost_usd": package_cost,
        "package_npv_usd": round(npv_avoided - package_cost),
        "package_payback_years": round(payback, 1) if payback is not None else None,
        "package_roi": round((npv_avoided - package_cost) / package_cost, 3)
        if package_cost
        else 0.0,
        "recommended_measure_ids": selected,
        "verdict": verdict,
        "rationale": rationale,
        "horizon_years": horizon_years,
        "discount_rate": discount_rate,
        "measures": appraisals,
    }


def model_warnings() -> list[str]:
    """Stated plainly on /api/meta/model so nobody demos placeholders by accident."""
    warnings: list[str] = []
    source = data_source()

    if source == "fixture":
        warnings.append(
            "PLACEHOLDER DATA: event counts and damage figures are generated from "
            "geography, not from NOAA or FEMA records. Do not present as real "
            "results. Drop data/processed/county_metrics.csv to switch to live data."
        )
    elif source == "partial":
        warnings.append(
            f"PARTIAL DATA: {counties_with_real_data()} of {len(FL_COUNTIES)} counties "
            "have real metrics; the rest still use placeholder values."
        )

    warnings.append(
        "County assignment falls back to nearest centroid when no county name is "
        "supplied, so coordinates near a border may be misassigned."
    )
    warnings.append(
        "Expected-loss figures are indicative, not actuarial average annual loss."
    )
    warnings.append(MITIGATION_ASSUMPTIONS)
    warnings.append(SIMULATION_METHOD)
    return warnings
