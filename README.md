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

Prices storms through vulnerability curves and a policy deductible, before and after a
mitigation upgrade. Shared response contract version 1.1, snake_case, documented at
`/docs`.

**The demo path** — send a portfolio, get a storm and what it costs:

- `POST /api/v1/simulate-storm` — draws a storm from the pool, prices it, and returns
  the payout rows plus the storm's full track for animation. Every candidate is a
  Category 3+ hurricane that strikes Florida. `selection: "random"` (default) draws one;
  `selection: "worst"` draws `stormsSampled` and keeps the most expensive.
- `GET /api/v1/storm-pool` — what the generator draws from, and how many storms had to
  be generated to fill it.

**Named storms** — for reproducible runs and tests:

- `GET /api/v1/storm-losses/example` — a complete worked run, no request body needed.
  Takes `?storm_id=`; defaults to a Category 4 Miami landfall.
- `POST /api/v1/storm-losses` — the contract. Accepts the frontend `Property` shape
  (`id`, `value`, `latitude`, `longitude`) directly.
- `GET /api/v1/storm-catalog` — the fixed catalog, with animatable tracks.
- `GET /api/v1/damage-curves` — the curves and policy template, with their provenance.

Read `metadata.scenario_selection` before quoting any number from `simulate-storm`. A
storm drawn at random and the worst of a hundred support completely different claims,
and the payout alone cannot tell them apart.

**Damage and payout are different events.** About half of these storms damage a home
and only ~14% clear a 5% hurricane deductible, so a client that shows only
`baseline_payout_usd` will display zero on most clicks while a real hurricane is on
screen. Show `baseline_damage_usd` too — "three homes damaged, insurer paid nothing,
the deductible absorbed it" is the product's actual argument.

Rebuild the storm pool (a few minutes; the committed one is fine for normal work):

```bash
cd backend
python scripts/build_storm_pool.py --target 600
```

Every damage curve currently shipped is an **assumed fixture**, and so is the wind
field that turns a storm-centre wind into a gust at a property. Both say so in their
own provenance, and every response carries `evidence_status`. Replace
`app/fixtures/damage_curves.json` when the research team supplies real curves, and
`app/wind.py` when a real wind field model arrives — `app/claims.py` is unaffected by
either, because it consumes wind exposures rather than tracks.

Re-import a new simulator run:

```bash
cd backend
python scripts/import_storm_catalog.py <simulator_output_dir> --storms SYN0155,SYN0697
```

## Frontend setup

```bash
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173
```

## Layout

```
backend/            FastAPI service, risk engine, ETL scripts
  app/
    main.py         Web layer: every endpoint
    schemas.py      Request and response contracts
    risk.py         County risk model and mitigation economics
    claims.py       Damage and insurer payout engine (pure, no HTTP)
    wind.py         Provisional wind field: storm centre -> gust at a property
    fixtures/       Curves, policy template, storm catalog, sample response
  scripts/          Adapters that import outside data
  tests/            pytest suite
  requirements.txt  Pinned dependencies
data/raw/           Downloaded source datasets (gitignored)
data/processed/     Build artifacts (gitignored except published profiles)
frontend/           React + TypeScript + Vite dashboard
```
