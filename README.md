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
  (`id`, `value`, `latitude`, `longitude`) directly. Optional `storms` prices storms
  sent with the request (such as generated ones) instead of the stored catalog.
- `GET /api/v1/storm-catalog` — the storms available to price, with animatable tracks.
- `GET /api/v1/damage-curves` — the curves and policy template, with their provenance.
- `POST /api/v1/storms/generate` — new storms from a starting point you choose (see
  below), returned in the catalog's shape.

The gust at each property comes from **wind_field**, the property-level wind model
from the hurricane simulator project, called by `app/wind.py`: a radial wind profile
around the storm centre (calm eye, strongest at the radius of maximum wind), moved
along the track in 15-minute steps so each home sees the storm's closest pass, with a
1.25 gust factor. Its storm size is still an assumed demonstration value, the same for
every storm, and every damage curve currently shipped is an **assumed fixture**. Both
say so in their own provenance, and every response carries `evidence_status`. Replace
`app/fixtures/damage_curves.json` when the research team supplies real curves;
`app/claims.py` is unaffected by the curves and the wind model alike, because it
consumes wind exposures rather than tracks.

`wind-field` and `hurricane-simulator` are not on PyPI. Their wheels live in
`backend/vendor/` and are pinned in `requirements.txt`. To upgrade either, put the new
wheel in `backend/vendor/`, update its pin, and reinstall. After a `wind-field`
upgrade, also regenerate `app/fixtures/sample_storm_losses_response.json` with the
command in `tests/test_storm_losses_api.py`.

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

In the dashboard, the Generate Storms card does this: **Pick on map** sets the start,
**Generate** runs it, and the new storms appear under Storm Scenario, where Simulate
Catastrophe animates and prices them like catalog storms.

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

## Layout

```
backend/            FastAPI service, risk engine, ETL scripts
  app/
    main.py         Web layer: every endpoint
    schemas.py      Request and response contracts
    risk.py         County risk model and mitigation economics
    claims.py       Damage and insurer payout engine (pure, no HTTP)
    wind.py         Wind field adapter: storm track -> gust at a property (wind_field)
    generator.py    Storm generation on request (hurricane_simulator, loaded on first use)
    fixtures/       Curves, policy template, storm catalog, sample response
  scripts/          Adapters that import outside data
  tests/            pytest suite
  vendor/           wind_field and hurricane_simulator wheels (not on PyPI)
  requirements.txt  Pinned dependencies
data/raw/           Downloaded source datasets (gitignored)
data/processed/     Build artifacts (gitignored except published profiles)
frontend/           React + TypeScript + Vite dashboard
```
