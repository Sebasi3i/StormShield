# StormShield

Property risk and hurricane mitigation intelligence for insurers.

StormShield helps insurers evaluate whether investing in preventative home upgrades
can reduce hurricane-related property damage and future claim exposure.

An insurer can select a portfolio of Florida properties, simulate hurricane scenarios,
and compare each property in its current state against specific mitigation upgrades
such as hurricane shutters and roof straps. StormShield models property-level wind
exposure, building damage, insurance payouts, mitigation costs, premium changes, and
insurer contributions.

The platform's Insurer Lab extends the analysis into a cost-benefit decision tool.
Insurers can compare homeowner-funded and insurer-co-funded mitigation programs,
evaluate the financial impact of individual hurricane scenarios, analyze illustrative
annual investment results, and allocate a limited mitigation budget across eligible
properties.

The goal is to help answer a practical question:

**Can an insurer invest in reducing a property's hurricane risk before a disaster in a
way that lowers potential claim exposure while creating value for both the insurer and
homeowner?**

StormShield is a decision-support prototype. The included insurer, policies, premium
credits, project costs, event probabilities, and other financial assumptions are
illustrative demonstration inputs unless otherwise documented. Modeled insurer payouts
are gross payouts before reinsurance, claims expenses, taxes, commissions, and capital
effects.

## How StormShield works

1. **Select properties** — Build a portfolio from the fictional Florida insurer's
   properties.

2. **Choose or generate hurricanes** — Use catalog storms or generate a batch of
   simulated Florida hurricane scenarios.

3. **Model property exposure** — Estimate the peak wind gust experienced by each
   property as the storm moves along its track.

4. **Estimate damage and insurer payout** — Apply building vulnerability curves,
   coverage limits, and deductibles to estimate physical damage and gross insurer
   payouts.

5. **Compare mitigation upgrades** — Re-run the same property and hurricane with
   upgrades such as shutters or roof straps and measure the resulting change in damage
   and insurer payout.

6. **Evaluate program economics** — Compare project costs, insurer grants, homeowner
   contributions, premium changes, avoided payouts, payback, and optional annual
   investment metrics.

7. **Allocate a mitigation budget** — Select a combination of eligible projects under
   a limited insurer budget using the modeled financial results.

## Tech stack

### Frontend

- React
- TypeScript
- Vite
- Leaflet
- React Leaflet
- OpenStreetMap
- Recharts

### Backend

- Python 3.12
- FastAPI
- Pydantic
- Uvicorn

### Risk modeling and data

- NumPy
- Pandas
- SciPy
- hurricane-simulator
- wind-field
- FEMA Hazus hurricane building loss functions
- NOAA HURDAT2 hurricane data
- Florida ASOS wind observations

### Testing and development

- pytest
- Git
- GitHub

## Requirements

- **Python 3.12** (backend) — use Python 3.12 to match the project's pinned
  dependencies and backend environment.
- **Node 20+** (frontend)

`backend/.python-version` records the required Python version. It is read automatically
by tools such as `uv` and `pyenv-win`. On Windows with Python's `py` launcher, specify
Python 3.12 explicitly.

Once `backend/.venv` is activated, `python` should resolve to the project's Python 3.12
environment.

## Quick start

StormShield runs as a FastAPI backend and a React frontend.

### Start the backend

On macOS or Linux:

```bash
cd backend
source .venv/bin/activate
python -m uvicorn app.main:app --reload --port 8000
```

On Windows PowerShell:

```powershell
cd backend
.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --port 8000
```

The API runs at:

- `http://127.0.0.1:8000`
- `http://127.0.0.1:8000/health` — health check
- `http://127.0.0.1:8000/docs` — interactive OpenAPI documentation

### Start the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the dashboard at:

`http://127.0.0.1:5173`

## Backend setup

### Windows

```powershell
# Install Python 3.12 if needed
py install 3.12

# Create and activate the virtual environment
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\Activate.ps1

# Confirm the correct Python version
python -V

# Install dependencies
pip install -r backend\requirements.txt
```

### macOS / Linux

```bash
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
python -V
pip install -r backend/requirements.txt
```

The backend dependencies include `wind-field`, which provides the property-level wind
model used by the storm-loss engine, and `hurricane-simulator`, which provides storm
generation. Their wheels are stored in `backend/vendor/` and pinned in
`requirements.txt`.

### Virtual environment activation

| Shell | Activate with |
|---|---|
| PowerShell | `.venv\Scripts\Activate.ps1` |
| cmd.exe | `.venv\Scripts\activate.bat` |
| Git Bash on Windows | `source .venv/Scripts/activate` |
| macOS / Linux | `source .venv/bin/activate` |

After activation:

```bash
python -V
```

should report Python 3.12.x.

### Run the API

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

### Run the tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest tests -q
```

Using `python -m pytest` ensures that pytest runs through the active virtual
environment's Python interpreter.

## Storm losses — damage and insurer payout

StormShield evaluates individual hurricane scenarios from the simulator catalog against
selected properties. For each property and storm, it estimates property-level wind
exposure, building damage, and gross insurer payout before and after an eligible
mitigation upgrade.

### Storm-loss endpoints

- `GET /api/v1/storm-losses/example` — returns a complete worked storm-loss run with no
  request body required. It accepts `?storm_id=` and defaults to the Category 4 Miami
  landfall.

- `POST /api/v1/storm-losses` — accepts the frontend `Property` shape directly,
  including `id`, `value`, `latitude`, `longitude`, `vulnerability_class`, and
  `roof_shape`. Each result identifies the property's existing features and the
  features added by the mitigation upgrade. An optional `storms` field allows generated
  storms to be priced instead of storms from the stored catalog.

- `GET /api/v1/storm-catalog` — returns the hurricane scenarios available to price,
  including animatable tracks.

- `GET /api/v1/damage-curves` — returns the vulnerability curves and policy template
  together with their provenance.

- `POST /api/v1/storms/generate` — generates new storms from a user-selected starting
  condition.

- `POST /api/v1/storms/generate-florida` — generates a batch of storms and searches for
  an ensemble in which at least the requested number cross Florida as major hurricanes.

## Wind and damage modeling

The gust at each property comes from **wind-field**, the property-level wind model used
by `app/wind.py`.

The model uses a radial wind profile around the storm center, including a calm eye and
the strongest winds near the radius of maximum wind. The storm is moved along its track
in 15-minute steps so that each property can experience the storm's closest pass.

The wind-model constants are calibrated using documented hurricane observations. See
`docs/calibration.md` for the fits, source data, methodology, and validation.

The calibration includes:

- Radius of maximum wind per storm from a model fitted to wind radii in NOAA's HURDAT2
  record.
- Gust factors measured at Florida ASOS stations during 19 hurricanes.
- Wind-profile decay and open-terrain land factors fitted against recorded peak gusts.
- Validation of the complete property-level wind modeling step against observations.

Relevant scripts include:

- `scripts/fit_storm_size.py`
- `scripts/fit_gust_factor.py`
- `scripts/calibrate_wind_field.py`
- `scripts/validate_wind_field.py`

The station data used for calibration lives in `data/calibration/`.

### Damage curves

The damage curves are based on FEMA Hazus hurricane building loss functions for
one-story masonry homes.

They are mapped to StormShield's vulnerability classes and mitigation upgrades by:

```text
scripts/build_damage_curves.py
```

The mapping and provenance are documented in the fixture metadata and
`docs/calibration.md`.

Responses using these curves carry:

```text
evidence_status: sourced
```

"Sourced" means the curves and assumptions are documented and reproducible. It does not
mean the results have been validated against the fictional portfolio's actual insurance
claims.

Curves are published separately for gable roofs, hip roofs, and their blend. A
property's `roof_shape` determines which curve is used.

An unlabeled property falls back to the blended curve, which assumes an equal gable/hip
mix. This fallback is an assumption and is not presented as the actual distribution of
Florida roof types.

`scripts/assign_demo_roof_shapes.py` gives the demonstration portfolio an illustrative,
seeded mix of both roof types.

`app/claims.py` consumes property wind exposures rather than hurricane tracks directly,
keeping the claims calculation separate from the wind model.

### Local model packages

`wind-field` and `hurricane-simulator` are not installed from PyPI. Their wheels live in:

```text
backend/vendor/
```

and are pinned in:

```text
backend/requirements.txt
```

To upgrade either package, place the new wheel in `backend/vendor/`, update the
dependency pin, and reinstall the backend requirements.

After a `wind-field` upgrade, regenerate
`app/fixtures/sample_storm_losses_response.json` using the workflow documented in
`tests/test_storm_losses_api.py`.

## Insurer Lab — mitigation economics and portfolio investment

The Insurer Lab models the financial tradeoff between hurricane mitigation costs,
premium incentives, and reduced insurer claim exposure for a fictional ten-property
portfolio.

It compares each property's current state with proposed mitigation projects and lets
the user evaluate homeowner-funded and insurer-co-funded programs.

The analysis includes:

- Project quotes
- Existing property mitigation features
- Proposed upgrades
- Insurer grants
- Homeowner contributions
- Current and post-upgrade premiums
- Homeowner premium savings
- Homeowner payback
- Modeled building damage
- Gross insurer payouts
- Avoided insurer payouts
- Optional illustrative annual investment metrics
- Budget-constrained project selection

The Lab can also optimize the selection of mitigation projects under a limited insurer
budget.

### Insurer endpoints

- `GET /api/v1/insurer/demo` — returns the fictional insurer, ten policies joined to
  the demonstration portfolio, presets, credit plan, proposals, program defaults,
  available storms, and provenance.

- `POST /api/v1/insurer/compare` — compares the current book with selected mitigation
  projects under homeowner-funded and insurer-co-funded program structures. Optional
  inputs include `annual_model`, `selected_proposal_ids`, `policy_ids`, program
  overrides, and deductible sensitivity.

- `POST /api/v1/insurer/optimize` — evaluates the mitigation program under the
  `one_event_or_none` annual model and selects projects within the insurer's available
  upfront budget.

Invalid identifiers, incompatible states, and malformed annual-model inputs return
422 responses identifying the invalid input.

## Premium and mitigation modeling

### `app/premium.py`

Handles:

- Existing and resulting mitigation features
- Premium credits
- Wind premiums
- Effective project costs
- Insurer grants
- Homeowner contributions
- Premium-only homeowner payback

Credits are looked up for the union of credited mitigation features. The demonstration
credit plan treats the combined-feature credit as its own tier rather than adding
individual percentages together.

A project quote covers only newly installed features.

If the cost of an upgrade is unknown, the value is returned as `null` with an
explanation rather than being silently treated as zero.

### Insurer fixtures

The sample-insurer inputs live in:

```text
app/fixtures/insurer_policies.json
app/fixtures/premium_credit_plan.json
app/fixtures/insurer_demo.json
```

They define ten policies joined to the demonstration portfolio, the illustrative
premium-credit plan, and two presets:

- `workbook_reference` — reproduces the reference workbook as written.
- `app_consistent_demo` — the default application preset. Post-2002 homes have
  class-inherent roof straps treated as installed and credited at baseline, an
  illustrative assumption applied consistently to both comparison arms.

### `app/mitigation_states.py`

Prices each policy's home in its current physical state and its resulting state after
the selected project using the same modeled wind exposure.

Each policy and event receives one current/resulting comparison pair, including no-op
pairs.

The model separates:

- Physical building damage
- Gross insurer payout
- Uninsured damage

This allows deductible changes to be represented as changes in who bears the loss
without incorrectly changing the modeled physical damage.

A property that already has roof straps and adds shutters is compared using the
appropriate current and resulting vulnerability states rather than adding independent
percentage reductions.

The pre-2002 shutters-plus-roof-straps package is also available through
`POST /api/v1/storm-losses` as:

```text
shutters_roof_straps
```

### `app/insurer.py`

Implements the financial comparison among three program structures:

1. Current book
2. Homeowner-funded mitigation
3. Insurer-co-funded mitigation

All program arms use the same selected projects and modeled hurricane exposure.

Each catalog storm is treated as an alternative event and is reported independently.
Storm results are not added together unless an explicit annual model is enabled.

A conditional first-policy-year result is available for each storm.

Annual metrics are available only when the explicit `one_event_or_none` annual model is
enabled. Its probability and storm weights are illustrative, editable demonstration
assumptions and are returned with the result.

Annual-mode outputs include:

- Expected avoided insurer payout
- Insurer net present value
- Break-even avoided payout
- Break-even annual event probability

The optimizer evaluates every subset of costed proposals, including the option to fund
nothing, and selects the subset with the highest modeled insurer NPV that fits within
the available upfront budget.

### Insurer workbook import

`scripts/import_insurer_workbook.py` rebuilds the insurer fixtures from:

```text
data/insurer_demo/workbook_extract.json
```

The importer checks the portfolio join field by field and verifies that the pricing
engine reproduces the workbook's reference totals.

The recorded reference values include:

```text
Workbook reference:
$105,895.00 -> $88,526.60

App-consistent demonstration:
$104,436.40 -> $87,476.85
```

A local copy of the original spreadsheet can also be checked against the recorded hash
using the script's `--workbook` option.

## Insurer Lab dashboard

The dashboard's **Insurer Lab** is the frontend client for the insurer-analysis
endpoints.

It opens using the policies corresponding to the properties currently selected on the
map. If nothing is selected, the whole fictional insurer book can be used.

The Lab provides:

- Policy selection
- Mitigation proposal selection
- Program and deductible assumptions
- Per-storm event comparisons
- Policy-level storm drill-downs
- Current-book, homeowner-funded, and insurer-co-funded comparisons
- Optional illustrative annual assumptions
- NPV and break-even analysis
- Budget optimization
- JSON export
- CSV export

Settings persist in the browser, while **Reset to seed** restores the demonstration
defaults.

All financial arithmetic is performed by the backend. The frontend displays and formats
the server's results.

Every rate, credit, zone, quote, and policy term in the fictional insurer demonstration
is an illustrative input.

## Generating storms

`POST /api/v1/storms/generate` runs the hurricane simulator from a starting condition
selected by the user.

Inputs include:

- `latitude`
- `longitude`
- `max_wind_kt`
- `start_date`
- `seed`
- `count` up to 10

The endpoint returns an ensemble in which every storm begins from the supplied starting
condition and follows its own seed.

The same request produces the same generated storms.

Starts over land or far outside the historical Atlantic genesis region are rejected
with an explanation.

A generated ensemble is a **what-if scenario set**, not a probabilistic hurricane
sample.

The simulator is loaded on the first generation request rather than API startup. The
first request therefore takes longer and increases the API process's memory use. Later
requests are substantially faster.

Generated storms are not stored server-side.

To price a generated storm, send the returned storm data in the `storms` field of:

```text
POST /api/v1/storm-losses
```

## Generating a Florida batch

`POST /api/v1/storms/generate-florida` searches for an ensemble containing simulated
storms that cross Florida.

The underlying hurricane simulator does not contain a dedicated "generate a Florida
hurricane" mode. Instead, StormShield searches candidate starting conditions.

Candidate genesis locations are sampled from historical Atlantic genesis positions,
including areas such as:

- Cape Verde region
- Caribbean
- Bahamas
- Gulf of Mexico

Each candidate receives the requested starting wind speed and generates an ensemble.

By default:

```text
count = 10
max_wind_kt = 70
min_florida_hits = 2
```

StormShield keeps the first ensemble in which at least `min_florida_hits` members cross
Florida at Category 3 strength or greater.

The entire ensemble is returned, including storms that do not hit Florida, so the user
can see the range of possible tracks produced from the same starting condition.

Each storm includes:

- `florida_hit`
- `florida_peak_wind_kt`
- `florida_first_time`

The response's `florida` block also records the selection criterion, qualifying storms,
and number of candidate starting conditions attempted.

### Florida major-hurricane criterion

For this endpoint, a qualifying Florida crossing means that the storm center is over
Florida land and has one-minute sustained winds of at least 96 knots at a six-hourly
track point.

Florida land is identified using `app/florida.py`, which combines a simplified Florida
outline with the simulator's land mask.

A storm that previously crossed another landmass can still qualify. A storm that
weakens below the threshold before reaching Florida does not.

### Reproducibility

Generation is controlled by `seed`.

The same request returns the same batch within the selected `season_year`, which
determines the generated storms' dates.

The search typically evaluates multiple candidate starts and gives up with a 422
response after 400 unsuccessful starts.

A lower starting wind speed can make a qualifying Florida batch harder to find.

Like every generated ensemble, the resulting batch is **selected for a condition** and
must not be interpreted as a sample from which annual hurricane probabilities can be
derived.

## Florida batch in the dashboard

The Florida storm generator appears under **Storm Scenario** as:

**Generate 10 Florida storms**

It appears alongside the stored catalog storms loaded from:

```text
GET /api/v1/storm-catalog
```

The user can select a seed and starting wind speed.

**Generate & Simulate** searches for a qualifying batch, animates the tracks together on
the map, and prices the generated storms in a single storm-loss run.

The same seed reuses the previously generated batch, allowing the simulation to be
replayed without repeating the search.

**New seed** generates another starting seed.

Users can click a storm track on the map or select a row in the Florida Batch list to
focus a specific storm. The status panel, Portfolio Impact, and Full Analysis views then
focus on that storm.

## Re-importing simulator runs

A new simulator run can be imported with:

```bash
cd backend
python scripts/import_storm_catalog.py <simulator_output_dir> --storms SYN0155,SYN0697
```

Tracks are trimmed to the catalog's map window only at their ends so that they remain
continuous.

The wind field does not bridge gaps in a stored track.

The shipped catalog predates that fix for one track: SYN0155 contains a skipped step
off Cape Hatteras. Results involving that section therefore include a warning that the
gap was not bridged.

## Frontend setup

Install the frontend dependencies:

```bash
cd frontend
npm install
```

Start the development server:

```bash
npm run dev
```

The frontend runs at:

```text
http://127.0.0.1:5173
```

By default, the dashboard connects to:

```text
http://127.0.0.1:8000
```

Set `VITE_API_BASE_URL`, for example in `frontend/.env.local`, to connect the dashboard
to a backend running elsewhere.

## Project structure

```text
backend/                       FastAPI service, risk engine, and modeling scripts
  app/
    main.py                    API routes and web layer
    schemas.py                 Request and response contracts
    risk.py                    Legacy county-level risk model
    claims.py                  Damage and insurer payout engine
    premium.py                 Mitigation credits, premiums, quotes, and grants
    mitigation_states.py       Current vs upgraded property states on the same wind
    insurer.py                 Program economics, annual NPV, and budget optimization
    wind.py                    Storm track -> property-level gust adapter
    generator.py               Hurricane generation
    florida.py                 Florida land-crossing logic
    fixtures/                  Curves, policies, storm catalog, and demo inputs

  scripts/                     Data import, calibration, and model-building scripts
  tests/                       pytest test suite
  vendor/                      Local wind-field and hurricane-simulator wheels
  requirements.txt             Pinned runtime dependencies
  requirements-dev.txt         Development and testing dependencies

data/
  calibration/                 Wind-model calibration observations and datasets
  insurer_demo/                Sample-insurer workbook extract
  raw/                         Downloaded source datasets
  processed/                   Generated data artifacts

docs/                          Calibration and model documentation

frontend/                      React + TypeScript + Vite dashboard
  src/
    api/                       Backend API clients
    components/                Dashboard and Insurer Lab components
    data/                      Frontend demonstration data
    types/                     TypeScript data contracts
    utils/                     Frontend utilities
```

## Important modeling notes

StormShield separates several concepts that should not be interpreted as equivalent:

- **Wind exposure** is the modeled peak gust experienced by a property.
- **Building damage** is the modeled physical loss to the structure.
- **Insurer payout** is the modeled amount paid after applying the policy's coverage and
  deductible.
- **Avoided payout** is the difference between modeled insurer payout before and after a
  mitigation upgrade.
- **Premium savings** represent the illustrative change in the homeowner's wind premium.
- **Insurer investment results** compare modeled claim benefits against grants, program
  costs, premium changes, and other configured financial assumptions.

Generated hurricane scenarios are what-if simulations unless an explicit annual model
is enabled.

The demonstration annual probabilities are illustrative assumptions and are not
presented as calibrated actuarial hurricane frequencies.

StormShield is a prototype decision-support platform. Its outputs should be interpreted
as modeled scenario results rather than guaranteed losses, savings, premiums, or
investment returns.