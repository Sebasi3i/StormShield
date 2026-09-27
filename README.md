# weather-risk-platform

Severe weather risk intelligence platform for property insurance.

An insurer submits a Florida property address and value. The platform analyzes
historical severe-weather events and economic losses for the surrounding county,
converts them into an explainable 0–100 risk score, and returns the score along
with the factors that produced it.

## Requirements

- **Python 3.12** (backend) — 3.13+ will not install. The pinned `numpy==2.0.2`
  ships no wheels above 3.12, so pip falls back to a source build and fails without
  MSVC. The geospatial libraries we add later (geopandas, shapely, pyproj) are also
  most reliably prebuilt for 3.12.
- **Node 20+** (frontend)

`backend/.python-version` records the required version. It is read automatically by
`uv` and `pyenv-win`, but **not** by Windows' Python Install Manager (`py`) — there,
be explicit with `py -3.12`. The real enforcement is the virtual environment: once
`backend/.venv` is activated, `python` is 3.12 regardless of what else is installed.

## Backend setup

```powershell
# One time: install the 3.12 runtime alongside whatever else you have
py install 3.12

# Create and activate the virtual environment
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\Activate.ps1
python -V                                    # must print 3.12.x

pip install -r backend\requirements.txt
```

This also installs `wind-field` (the wind model the storm-loss endpoints use) and
`hurricane-simulator` (storm generation) from the wheels in `backend/vendor/`; neither
is on PyPI.

macOS / Linux:

```bash
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
```

Activating the environment depends on the shell, and getting it wrong is the most
common way to end up running a different Python:

| Shell | Activate with |
|---|---|
| PowerShell | `.venv\Scripts\Activate.ps1` |
| cmd.exe | `.venv\Scripts\activate.bat` |
| Git Bash / macOS / Linux | `source .venv/Scripts/activate` (`.venv/bin/activate` on Unix) |

After activating, `python -V` must print 3.12.x. If it prints anything else, the
activation did not take and every command below will use the wrong interpreter —
`fastapi` and `starlette` will appear to be missing. Skipping activation entirely and
naming the interpreter works from any shell:

```
.venv\Scripts\python.exe -m pytest tests -q
```

Run the API:

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

- http://127.0.0.1:8000/health — liveness
- http://127.0.0.1:8000/docs — interactive OpenAPI docs

Run the tests:

```bash
cd backend
pip install -r requirements-dev.txt    # pytest and httpx, on top of requirements.txt
python -m pytest tests -q
```

### Storm losses — damage and insurer payout

Prices individual storms from the simulator catalog through vulnerability curves and a
policy deductible, before and after a mitigation upgrade. Shared response contract
version 1.1, snake_case, documented at `/docs`.

- `GET /api/v1/storm-losses/example` — a complete worked run, no request body needed.
  Takes `?storm_id=`; defaults to the Category 4 Miami landfall.
- `POST /api/v1/storm-losses` — the contract. Accepts the frontend `Property` shape
  (`id`, `value`, `latitude`, `longitude`, plus `vulnerability_class` and `roof_shape`)
  directly, and every row says which features the home already has and which the
  upgrade adds, so a client can label an upgrade by what it changes. Optional `storms` prices storms
  sent with the request (such as generated ones) instead of the stored catalog.
- `POST /api/v1/storm-losses/average-year` — an average year for the demo properties
  over the simulated storm climatology (below): expected yearly repair cost and
  insurance cover as they are, the chance of damage in a year, once-per-N-years repair
  costs, and what each upgrade would save per year. The report's "Average year" tab.
- `GET /api/v1/storm-catalog` — the storms available to price, with animatable tracks.
- `GET /api/v1/damage-curves` — the curves and policy template, with their provenance.
- `POST /api/v1/storms/generate` — new storms from a starting point you choose (see
  below), returned in the catalog's shape.
- `POST /api/v1/storms/generate-florida` — a batch of storms from a random starting
  point, of which at least two cross Florida as major hurricanes (see below).

The gust at each property comes from **wind_field**, the property-level wind model
from the hurricane simulator project, called by `app/wind.py`: a radial wind profile
around the storm centre (calm eye, strongest at the radius of maximum wind), moved
along the track in 15-minute steps so each home sees the storm's closest pass. Its
constants are calibrated (see `docs/calibration.md` for the fits, the data and the
validation): the radius of maximum wind per storm from a model fitted to the wind radii
in NOAA's HURDAT2 record (`scripts/fit_storm_size.py`); the gust factor measured at
Florida ASOS stations during 19 hurricanes (`scripts/fit_gust_factor.py`); and the
profile's decay exponent and an open-terrain land factor fitted jointly to the peak
gusts those stations recorded (`scripts/calibrate_wind_field.py`), with
`scripts/validate_wind_field.py` recording how the whole step compares with the
observations. The station data lives in `data/calibration/`. The damage curves are
FEMA Hazus hurricane building loss functions for one-story masonry homes, mapped to
the platform's classes and upgrades by `scripts/build_damage_curves.py` (the mapping is
in the fixture's provenance and `docs/calibration.md`), so responses now carry
`evidence_status: sourced`. Sourced means documented and reproducible, not validated
against this portfolio's claims. Each curve is published separately for gable roofs,
hip roofs, and their blend; a property's `roof_shape` picks the matching one, and an
unlabeled property falls back to the blend, an assumed equal mix with no source for
Florida's actual gable/hip split (`scripts/assign_demo_roof_shapes.py` gives the demo
portfolio an illustrative, seeded-random mix of both so this has something to exercise).
`app/claims.py` is unaffected by the curves and the wind model alike, because it
consumes wind exposures rather than tracks.

`wind-field` and `hurricane-simulator` are not on PyPI. Their wheels live in
`backend/vendor/` and are pinned in `requirements.txt`. To upgrade either, put the new
wheel in `backend/vendor/`, update its pin, and reinstall. After a `wind-field`
upgrade, also regenerate `app/fixtures/sample_storm_losses_response.json` with the
command in `tests/test_storm_losses_api.py`.

### Sample insurer — premium credits for mitigation

The groundwork for the Insurer Lab: a fictional insurer covering the ten demo
properties, with the workbook's premium-credit tiers and project quotes, so avoided
payouts can later be set against the premium an insurer gives up to encourage upgrades.
Endpoints, all under the same prefix as the rest of the API and documented at `/docs`:

- `GET /api/v1/insurer/demo` — the insurer, its ten policies joined to the demo
  portfolio, both presets, the credit plan, proposals, program defaults, the storms
  that can be run, and provenance. Everything a client needs to build a request.
- `POST /api/v1/insurer/compare` — current book versus the selected projects,
  homeowner-funded and co-funded, per storm. Optional `annual_model`,
  `selected_proposal_ids`, `policy_ids`, `program` overrides and a `deductible_fraction`
  sensitivity. Bad ids, state conflicts and a malformed annual model are 422s that
  name the input.
- `POST /api/v1/insurer/optimize` — the same request under the `one_event_or_none`
  model, plus the budget selection and the comparison re-run on the chosen subset.

- `app/premium.py` — feature union -> credit -> wind premium, effective project cost,
  grants and the homeowner's premium-only payback. Credits are looked up for the union
  of credited features (both features earn 25%, not 8% + 12%); a quote covers new
  features only, and an unknown cost is null with a reason, never zero.
- `app/fixtures/insurer_policies.json`, `premium_credit_plan.json`, `insurer_demo.json`
  — ten policies joined to the demo portfolio, the credit plan, and two presets:
  `workbook_reference` (the workbook as written, premium-only) and the default
  `app_consistent_demo` (post-2002 homes have their class-inherent roof straps installed
  and credited at baseline, a demo assumption applied to both arms).
- `app/mitigation_states.py` — prices a policy's home as it is and as its project
  leaves it, on the same wind: one current/resulting pair per policy and event, no-op
  pairs included, with payout and uninsured damage split out so a deductible change
  shows as a transfer. A home that already has straps and adds shutters is compared
  straps-curve to package-curve, never baseline to a sum of two reductions. For that
  the curve set gained the pre-2002 shutters-plus-straps package (15 -> 18 curves), and
  every curve now records the features its building has; the package is also offered
  by `POST /api/v1/storm-losses` as the upgrade `shutters_roof_straps`.
- `app/insurer.py` — the economics: three arms (current book, homeowner-funded,
  insurer co-funded) on the same selected projects and the same wind, each catalog
  storm reported as an alternative event and never added across storms. A conditional
  "if this storm occurs in the first policy year" figure is always available. Annual
  figures (expected avoided payout, insurer NPV, break-even avoided payout, and the
  break-even annual event probability) exist only under the explicit
  `one_event_or_none` model, whose probability and storm weights are an invented,
  editable demo assumption carried back with every result. `optimize` enumerates every
  subset of the costed proposals and picks the highest insurer NPV within the upfront
  budget, the empty subset included.
- `app/climatology.py` and `scripts/build_storm_climatology.py` — the frequency the
  catalog cannot give. The script asks the simulator for thousands of storms whose
  starting points are drawn at random from the whole historical record, so the sample
  is unselected (most never approach Florida), runs each through the calibrated wind
  field, and stores only the peak gust at each demo property
  (`app/fixtures/storm_climatology.json`). Pricing from gusts is instant, so the
  service re-prices any project selection or deductible against the whole sample on
  request. Expected yearly figures are the mean per storm times a storms-per-year rate
  for the same population; the fixture records the whole record's rate and the last
  thirty years', the default. In the Lab this is the "simulated climate" annual option,
  the defensible alternative to the invented probability, and it reports break-even as
  storms per year. Rebuild after adding a property or changing the wind fixtures:

  ```bash
  python scripts/build_storm_climatology.py          # 5,000 storms, seed 2026, a few minutes
  ```
- `scripts/import_insurer_workbook.py` — rebuilds those fixtures from
  `data/insurer_demo/workbook_extract.json`, checks the join to the portfolio field by
  field, and refuses to write unless the engine reproduces the workbook's totals to the
  cent ($105,895.00 -> $88,526.60 current-to-result wind premium for the reference
  preset; $104,436.40 -> $87,476.85 for the normalized one). `--workbook` verifies a
  local copy of the spreadsheet against the recorded hash.

The dashboard's **Insurer Lab** (the switch in the header) is the client for these:
the book and its proposals with tick boxes, an assumptions drawer that edits the
program and the deductible sensitivity, each storm as an alternative event with a
policy drill-down, the three program arms side by side, an explicit switch for the
illustrative annual assumptions that reveals NPV and break-even, "Optimize within
budget", and JSON/CSV export. Settings persist in the browser; "Reset to seed" restores
the specification's defaults. All arithmetic is the server's; the screen formats it.

Every rate, credit, zone, quote and policy term is an illustrative workbook input.

### Generating storms

`POST /api/v1/storms/generate` runs the hurricane simulator from a starting point you
choose (`latitude`, `longitude`, `max_wind_kt`, `start_date`, `seed`, `count` up to 10)
and returns an ensemble: every storm starts there, and each follows its own seed. The
same request always returns the same storms. Starts over land, or far from where
Atlantic storms have formed, are rejected with a reason. An ensemble is a what-if from
one starting condition, not a probabilistic sample, and the response says so.

The simulator is loaded on the first generate request, not at start-up: that request
takes a couple of seconds and raises the API process's memory to about 1 GB (the
simulator's global land/sea map). Later requests take milliseconds. Nothing is stored
server-side; to price a generated storm, send it back in the `storms` field of
`POST /api/v1/storm-losses`.

### Generating a Florida batch

`POST /api/v1/storms/generate-florida` answers "give me ten random storms that hit
Florida". The simulator has no such mode: it samples starting points from the whole
Atlantic record and knows land from sea but not one state from another. So the API
searches. It draws candidate starting points at random from historical genesis positions
(Cape Verde, the Caribbean, the Bahamas, the Gulf and so on, with the simulator's own
jitter), gives each the requested start wind (`max_wind_kt`, default 70), runs a
`count`-member ensemble (default 10) from it, and keeps the first ensemble in which at
least `min_florida_hits` storms (default 2) cross Florida at Category 3 or stronger. The
whole batch is returned: every storm carries `florida_hit`, `florida_peak_wind_kt` and
`florida_first_time`, and the `florida` block records the criterion, the hits and how many
starts were tried. Members that miss Florida are returned too, so the batch shows the
spread of paths from one origin.

"Crosses Florida at Category 3 or stronger" means the storm centre is over Florida land
(`app/florida.py`, a simplified outline of the state, combined with the simulator's land
mask) with one-minute sustained wind of at least 96 kt at some six-hourly track point. A
storm that came ashore in Cuba first still counts; one that weakened below 96 kt before
reaching Florida does not.

Everything follows from `seed`, so the same request returns the same batch within a
season year (`season_year`, defaulting to the current year, sets the storms' dates). The
search typically tries a few dozen starts and takes a few seconds with the default start
wind; it gives up with a 422 after 400 starts, which a weaker start wind makes more
likely. Like every generated set, a batch is a **selected** subset: do not derive annual
rates from it.

In the dashboard, the generator is the fourth entry under Storm Scenario, **Generate 10
Florida storms**, next to the three catalog tracks (loaded from `GET /api/v1/storm-catalog`).
Choosing it shows the seed and start wind; **Generate & Simulate** runs the search, then
animates all ten tracks together on the map and prices them in one storm-losses run.
Florida hits are drawn in red. The same seed reuses the batch already generated, so Replay
does not search again; **New seed** picks another. Click a track on the map, or a row in
the Florida Batch list, to focus that storm: the status panel, Portfolio Impact and Full
Analysis then show that storm.

Re-import a new simulator run:

```bash
cd backend
python scripts/import_storm_catalog.py <simulator_output_dir> --storms SYN0155,SYN0697
```

Tracks are trimmed to the catalog's map window at their ends only, so they stay
continuous; the wind field will not bridge a gap in a track. The shipped catalog
predates that fix: SYN0155's stored track skips one step off Cape Hatteras, so its
results carry a warning saying the gap was not bridged.

## Frontend setup

```bash
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173
```

The dashboard talks to `http://127.0.0.1:8000` by default. Set `VITE_API_BASE_URL` (for
example in `frontend/.env.local`) to point it at another backend.

## Layout

```
backend/            FastAPI service, risk engine, ETL scripts
  app/
    main.py         Web layer: every endpoint
    schemas.py      Request and response contracts
    risk.py         County risk model and mitigation economics
    claims.py       Damage and insurer payout engine (pure, no HTTP)
    premium.py      Sample insurer: mitigation credits -> wind premium, quotes, grants (pure)
    mitigation_states.py  Sample insurer: current vs upgraded physical state on the same wind (pure)
    insurer.py      Sample insurer: program arms, event results, annual NPV, budget optimizer (pure)
    climatology.py  Expected yearly losses over the simulated storm sample (pure, vectorised)
    wind.py         Wind field adapter: storm track -> gust at a property (wind_field)
    generator.py    Storm generation on request (hurricane_simulator, loaded on first use)
    florida.py      Florida outline: is this track point over Florida?
    fixtures/       Curves, policy template, storm catalog, sample response, insurer demo
  scripts/          Adapters that import outside data, and the calibration fits
  tests/            pytest suite
  vendor/           wind_field and hurricane_simulator wheels (not on PyPI)
  requirements.txt  Pinned dependencies
data/calibration/   Station observations the wind constants are fitted to
data/insurer_demo/  Workbook extract the sample-insurer fixtures are built from
data/raw/           Downloaded source datasets (gitignored)
data/processed/     Build artifacts (gitignored except published profiles)
docs/               Calibration notes: what each constant rests on
frontend/           React + TypeScript + Vite dashboard
```
