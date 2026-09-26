# Data Layer Plan

How historical weather and loss data gets into the backend, what shape it takes,
and what the API serves from it.

**Scope:** Florida (state FIPS `12`), 67 counties. Analysis window **1996–2025**
for event frequency and damage; **1900–2025** for hurricane tracks.

Why 1996: NOAA only coded the full set of ~50 event types from 1996 onward. Before
that, coverage is tornado/hail/thunderstorm-wind only, so a longer window would
bias flood and hurricane frequency downward in a way that looks like real signal.
Hurricane *tracks* have no such problem, so those go back further.

---

## Core design decision: precompute, then serve lookups

ETL runs **offline**, on a developer machine, and writes a small set of build
artifacts. At request time the API does: geocode → county FIPS → table lookup →
score → respond. No NOAA or FEMA call sits on the request path.

Two reasons. Responses land in single-digit milliseconds instead of seconds. And
the demo cannot be broken by a federal API having a bad afternoon — which is not a
hypothetical risk on a weekend when nobody is staffing those services.

The only live external call is geocoding, and that is cached and has an offline
fallback (see §4).

---

## 1. Sources

Every fetcher writes to `data/raw/<source>/`, records a manifest row, and is a
no-op if the file is already present with a matching checksum unless run with
`--force`.

### 1.1 NOAA NCEI Storm Events — the backbone

Bulk CSVs, one set per year, at `ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/`.
We need the `details` files; `locations` and `fatalities` are optional extras.

Fields we use: `EVENT_ID`, `EPISODE_ID`, `STATE_FIPS`, `CZ_TYPE`, `CZ_FIPS`,
`CZ_NAME`, `EVENT_TYPE`, `BEGIN_DATE_TIME`, `END_DATE_TIME`, `DAMAGE_PROPERTY`,
`DAMAGE_CROPS`, `INJURIES_DIRECT`, `DEATHS_DIRECT`, `MAGNITUDE`, `MAGNITUDE_TYPE`,
`TOR_F_SCALE`, `BEGIN_LAT`, `BEGIN_LON`, `EVENT_NARRATIVE`.

Three problems that must be handled explicitly:

**Filenames embed a creation date** (`..._d2023_c20240416.csv.gz`) that changes
whenever NCEI republishes. The fetcher must list the directory and regex-match on
year, not construct filenames.

**`DAMAGE_PROPERTY` is a coded string**, not a number: `"2.10K"`, `"1.5M"`,
`"12.00B"`, `""`, `"0.00K"`. A parser handles the K/M/B suffixes. Critically,
**blank is not zero** — it means damage was never recorded. Coercing blanks to 0
silently understates damage in exactly the older records where it is most common,
so blanks are stored as `NULL` and counted separately.

**`CZ_TYPE` decides whether `CZ_FIPS` is even a county code.** This is the single
biggest trap in the dataset:

- `CZ_TYPE = 'C'` → `CZ_FIPS` is a county FIPS. Join directly.
- `CZ_TYPE = 'Z'` → `CZ_FIPS` is an **NWS forecast zone number, which is not a
  county code and does not correspond to one.**

Hurricanes, tropical storms, storm surge, coastal flood and high wind are
predominantly logged as `Z` events. So a naive join on `CZ_FIPS` will silently
misassign the most important hazard class in the entire product, and it will *look*
like it worked. Resolving it needs the NWS zone-to-county correlation file (§1.5),
plus an allocation rule (§3.2).

### 1.2 NOAA HURDAT2 — hurricane tracks

Single plaintext file from NHC (`nhc.noaa.gov/data/hurdat/`). Filename encodes the
revision date, so again: list and regex, don't construct.

Format is a header line per storm (basin/number/year, name, row count) followed by
six-hourly track rows: date, time, record identifier (`L` = landfall), status
(`TD`/`TS`/`HU`/`EX`/...), lat, lon, max sustained wind in knots, min pressure.

This is what makes the product more than a county lookup table. From tracks we
compute, for a specific property coordinate, the **distance to every historical
hurricane track weighted by storm intensity at closest approach**. That is a genuine
property-level feature, and it is why two addresses in the same county can differ —
and why the map will show a real coastal-to-inland gradient rather than 67 flat
county blocks.

### 1.3 FEMA OpenFEMA — declarations and paid losses

JSON/CSV REST, no API key. Supports `$filter`, `$select`, `$top` (max 10,000),
`$skip`, `$inlinecount`.

Dataset versions change (`/v1/` vs `/v2/`), so the fetcher resolves the current
version from the `OpenFemaDataSets` endpoint rather than hardcoding paths.

- **`DisasterDeclarationsSummaries`** — declaration counts per county with
  `incidentType` (Hurricane / Flood / Severe Storm / Tornado). Cheap, small, and a
  good independent cross-check on the NOAA event counts.
- **`HousingAssistanceOwners`** — county-level `totalDamage`,
  `averageFemaInspectedDamage`, valid registration counts. Real inspected dollar
  damage at county grain.
- **`NfipClaims`** — the flood signal, and the most credible dataset here for an
  insurance audience: roughly 2.6M *actual paid* NFIP claims with `dateOfLoss`,
  `countyCode`, `floodZone`, `amountPaidOnBuildingClaim`,
  `amountPaidOnContentsClaim`, `totalBuildingInsuranceCoverage`. Filter
  `state eq 'FL'` server-side — pulling this unfiltered is gigabytes.

  **Do not treat NFIP lat/lon as point data.** It is truncated to ~0.1°, roughly
  11 km, specifically to de-identify policyholders. Used at county + flood-zone
  grain it is excellent; used as a coordinate it is fiction.

### 1.4 FEMA NFHL — flood zone at a point

ArcGIS REST service
(`hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer`). Query the
flood-hazard-zone layer at the property coordinate to get its zone: `VE` / `AE` /
`A` = high risk, `X` = minimal. Resolve the layer id from the service's own JSON
metadata; the numbering has changed across service revisions.

High demo value — "this address sits in Zone AE" is concrete in a way a county
average is not. But it is a live third-party dependency, so: cache every result, and
degrade to the county-average flood score on timeout rather than failing the
request.

### 1.5 Supporting reference data

- **NWS zone-to-county correlation** (`weather.gov/gis/ZoneCounty`) — required to
  fix the `CZ_TYPE = 'Z'` problem. Filename is date-stamped; list and regex.
- **Census ACS 5-year** (`api.census.gov`) — `B01003_001E` population,
  `B25001_001E` housing units, `B25077_001E` median home value, for `county:*` in
  `state:12`. Needed to normalize damage (§3.4).
- **Census cartographic boundaries** — county polygons for the choropleth and for
  point-in-polygon county assignment. The 500k or 20m resolution is plenty; full
  TIGER is needlessly heavy for 67 counties.
- **CPI-U annual averages, 1996–2025** — committed to the repo as a small static
  CSV rather than fetched. It is ~30 numbers that never change retroactively, and it
  removes a network dependency from the build for no real cost.

### 1.6 Deliberately excluded

- **SHELDUS** — best-in-class county loss data, but paid. Out of scope.
- **NOAA Billion-Dollar Disasters** — state-level only. Useful narrative color for
  the UI, not usable as a county feature.

---

## 2. Storage

**SQLite**, one file at `data/processed/weather_risk.db`, accessed through
SQLAlchemy models.

Reasoning: the working set is 67 counties and a few hundred thousand event rows —
comfortably under 200 MB. SQLite needs zero setup, behaves identically for all four
teammates, and cannot produce a "works on my machine" connection problem the night
before judging. Keeping SQLAlchemy models means moving to Postgres later is a
connection-string change.

**No PostGIS.** The geometry work is point-in-polygon against 67 county polygons and
great-circle distance to track points — shapely and numpy do that in milliseconds in
memory. PostGIS earns its place at portfolio scale; at MVP scale it is an afternoon
of setup that buys nothing.

### 2.1 Tables

| Table | Grain | Purpose |
|---|---|---|
| `counties` | county | FIPS, name, population, housing units, median home value, centroid, coastal flag |
| `nws_zone_county` | zone × county | Crosswalk fixing `CZ_TYPE='Z'` events |
| `storm_events` | event | Cleaned NOAA events, parsed damage, assigned county, hazard class, allocation provenance |
| `hurricane_tracks` | storm × timestep | HURDAT2 positions, wind, pressure, landfall flag |
| `hurricane_county_exposure` | storm × county | Min distance, wind at closest approach — derived |
| `fema_declarations` | declaration × county | Declaration counts by incident type |
| `fema_housing_assistance` | disaster × county | Inspected damage dollars |
| `nfip_claims` | claim | FL-only paid flood claims |
| `county_features` | county × feature | **Long format:** raw, log, winsorized, percentile, normalized |
| `county_scores` | county × model version | Sub-scores and overall |
| `geocode_cache` | address hash | Cached geocoder results |
| `etl_manifest` | source × run | URL, checksum, row count, date range, fetch time |

Two of those deserve explanation.

**`county_features` is long, not wide** — one row per county per feature, carrying
the value at every stage of the transform. That is what makes explainability
structural rather than cosmetic: the API can show the raw number, the Florida
percentile, the normalized score, the weight, and the resulting contribution, all
read straight out of a table instead of recomputed in a text template. A judge
asking "where did 82 come from" gets arithmetic, not prose.

**`etl_manifest` is not bookkeeping overhead.** It is what lets us answer "how much
data is behind this, and how current is it" on stage, and what makes a bad build
detectable instead of mysterious.

---

## 3. Pipeline

Stages are separate scripts under `backend/scripts/`, each idempotent and
individually runnable. `python -m scripts.build_all` runs the chain; `--sample`
builds a small DB fast for a teammate who just needs something to develop against.

```
fetch → validate → parse → allocate → derive → aggregate → features → score → publish
```

### 3.1 Parse

Damage strings to integer cents. Event types to hazard classes. The **hazard class
mapping lives in a version-controlled config file**, not inline in code: collapsing
~50 NOAA event types into hurricane / flood / severe storm (and an explicit excluded
set — wildfire, drought, winter weather, extreme heat) is a *modeling* decision that
a judge may well question, so it needs to be legible and citable rather than buried
in a dict literal.

### 3.2 Allocate — the zone-to-county fix

For `CZ_TYPE='C'`, county is direct. For `CZ_TYPE='Z'`, the zone maps to one or more
counties, and the event's damage is apportioned across them weighted by housing
units.

Every row records `allocation_method` and `allocation_weight`. This matters because
the apportionment is an assumption, not a measurement, and it should be auditable
and swappable rather than invisible. Where an event carries usable `BEGIN_LAT/LON`,
point-in-polygon takes precedence over zone allocation, and that is recorded too.

### 3.3 Derive — hurricane exposure

For each HURDAT2 storm and each county centroid: minimum distance to the track and
the storm's max sustained wind at that closest approach. Filter to storms passing
within a threshold radius. The same function runs at request time against a geocoded
property coordinate, so the county view and the property view use one code path
rather than two that can drift apart.

### 3.4 Features — three decisions that determine whether the numbers are credible

**Normalization is percentile rank plus a log transform, not min-max.** A single
multi-billion-dollar hurricane figure in Miami-Dade compresses all 66 other counties
toward zero under min-max, producing one red county on a sea of green and a model
that discriminates nothing. Pipeline: `log1p` → winsorize at the 95th percentile →
scale, and store the **within-Florida percentile** alongside, because "higher
hurricane exposure than 94% of Florida counties" is both more honest and more
readable than a bare index.

**Damage is CPI-adjusted and exposure-normalized.** Raw damage totals are partly a
wealth-and-population proxy: large affluent coastal counties simply have more dollars
available to lose. Left raw, the model partly rediscovers where expensive houses are
and calls it hazard. So: adjust to 2025 dollars, express frequency as events per year
over a fixed window, and normalize damage per housing unit. Store both raw and
normalized — the raw totals are what an underwriter wants to *see*, the normalized
values are what the score should be *built on*.

**Property value never enters the hazard score.** A $2M and a $200k house on the same
street face the same weather; giving them different risk scores is wrong, and an
underwriter will spot it in seconds. Value enters only on the exposure side:
`estimated exposure = property value × county expected damage ratio`. That dollar
figure ships labeled as **indicative, not an actuarial AAL** — overstating it is the
fastest available way to lose credibility with an insurance judge.

### 3.5 Publish

Final stage writes `data/processed/fl_county_profiles.json` — 67 rows, roughly
100 KB — and **this artifact is committed to the repo**, unlike everything else under
`data/processed/`. Requires a `.gitignore` exception.

Why commit a build output: a fresh clone then serves real data with no ETL run, no
downloads, and no credentials. New teammates are productive immediately, and the demo
has a guaranteed-working floor even if the database build is mid-edit when it is time
to present.

---

## 4. Geocoding

**Census Geocoder** (`geocoding.geo.census.gov/geocoder/geographies/onelineaddress`,
`benchmark=Public_AR_Current`, `vintage=Current_Current`). Returns coordinates **and
county FIPS in a single keyless call** — which is the entire location-processing step,
with no API key to provision and no rate limit to apologize for mid-demo. Nominatim
would need a 1 req/s courtesy limit and a custom user-agent; not worth it when the
Census service returns the county directly.

Three-tier degradation, because address input is the one part of the flow a judge
will definitely poke at:

1. Census Geocoder, result written to `geocode_cache`.
2. On miss or timeout: FL ZIP-to-county crosswalk bundled in the repo. Loses the
   property-level hurricane-track feature, keeps every county-level score.
3. On total failure: explicit error naming the address, never a silent default to
   some arbitrary county.

---

## 5. Volume and runtime

| Dataset | Rows | Notes |
|---|---|---|
| FL storm events, 1996–2025 | ~60–80k | 30 annual gzipped files |
| HURDAT2 track points | ~50k | Whole Atlantic basin, one file |
| FL NFIP claims | ~200–400k | Largest download; filter server-side |
| FEMA declarations / assistance | ~10k | Small |
| Counties + features + scores | ~2k | Trivial |

Full cold build: a few minutes, dominated by download rather than compute. Warm
rebuild from cached raw files: seconds.

---

## 6. Known risks

| Risk | Mitigation |
|---|---|
| `CZ_TYPE='Z'` misassignment | Zone crosswalk + recorded allocation provenance (§3.2) |
| NOAA damage magnitude typos (`K` entered as `B`) | Winsorize, flag outliers, keep raw value visible |
| Blank damage read as zero | Store `NULL`, count unrecorded separately |
| NCEI filename churn | Directory listing + regex, never constructed paths |
| OpenFEMA `$top` cap and pagination | Paginate on `$skip`, verify against `$inlinecount` |
| NFIP coordinates mistaken for point data | County + flood-zone grain only (§1.3) |
| NFHL service flakiness | Cache; degrade to county average |
| Geocoder failure on odd addresses | Three-tier fallback (§4) |
| Min-max normalization flattening the model | Log + winsorize + percentile (§3.4) |
| Damage as a wealth proxy | CPI adjust + per-housing-unit normalization (§3.4) |

---

## 7. On machine learning

Not in the MVP, and the reason is not time. A supervised model needs a labeled
target, and the honest target here — actual insured loss per property — is exactly
the data we do not have. Training on aggregated county damage and presenting the
output as property-level risk would produce something that scores well on its own
resampled data and means very little in the world.

A transparent weighted model over real historical data is both more defensible and
more explainable than a gradient booster fit to 67 rows. If ML enters later, the
defensible framing is a model predicting county damage-per-housing-unit from hazard
features, used to **sanity-check the hand-set weights** rather than to replace them —
and that is worth doing only once the pipeline above is finished.
