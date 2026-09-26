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

Run the API:

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

- http://127.0.0.1:8000/health — liveness
- http://127.0.0.1:8000/docs — interactive OpenAPI docs

## Frontend setup

```bash
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173
```

## Layout

```
backend/            FastAPI service, risk engine, ETL scripts
  app/              Application code
  requirements.txt  Pinned dependencies
data/raw/           Downloaded source datasets (gitignored)
data/processed/     Build artifacts (gitignored except published profiles)
frontend/           React + TypeScript + Vite dashboard
```
