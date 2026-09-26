# hurricane_simulator (V1)

A Monte Carlo synthetic tropical-cyclone track generator for the
Atlantic basin. This is a pure **hazard generator**: given historical
storm observations, it produces a catalog of synthetic storm tracks and
a per-storm summary. It intentionally does nothing else.

## Module boundary

```text
HURRICANE SIMULATOR   (this package)
        |
        v
storm trajectory + intensity     storm_id, t, lat, lon, V_max
        |
        v
WIND FIELD MODEL             (next module -- not part of this package)
        |
        v
wind at each property
        |
        v
DAMAGE MODEL                  (not part of this package)
        |
        v
insurance claims
```

The simulator only ever hands the next module `storm_id, t, lat, lon,
V_max` (plus a few convenience columns -- see below). **It does not
compute property-level wind, damage, or claims.** That separation is
enforced by what this package exports (`hurricane_simulator/__init__.py`)
-- keep it that way when extending this code; put wind-field and damage
logic in their own modules/packages that *consume* this one's output.

## Interface

```python
from hurricane_simulator import simulate_hurricanes, load_hurdat2

historical_data = load_hurdat2("hurdat2-1851-2024.txt")  # or your own DataFrame

tracks, summary = simulate_hurricanes(
    historical_data,
    num_storms=1000,
    seed=42,
    time_step_hours=6,
    max_duration_days=25,
    basin="Atlantic",
)
```

### Visualize a simulation

Install the optional plotting dependency and generate a figure from the
simulation result. The figure combines density from all simulated track
points, a reproducible sample of individual paths, and peak-intensity and
duration distributions:

```bash
pip install -e ".[visualization]"
```

```python
import hurricane_simulator as hs

result = hs.simulate_hurricanes(historical_data, num_storms=1000, seed=42)
figure = hs.plot_simulation(result, max_tracks=100, seed=42)
figure.savefig("synthetic_storm_visualization.png", dpi=160)
```

`plot_simulation()` also accepts the track and summary DataFrames separately.
The plotting dependency is optional; the simulation itself does not require
Matplotlib.

### Interactive track predictor

Run a local browser dashboard using historical observations to fit the
analog predictor:

```bash
python examples/run_dashboard.py path/to/hurdat2.txt
```

With no file argument, the example uses the real NOAA data in `data/` if
present, falling back to the synthetic sample fixture otherwise. Click
the map to set the storm's starting location, choose initial intensity, date,
and random seed, then start the prediction. The dashboard animates the
predicted six-hourly path and displays the storm's position, maximum wind,
category, and land interaction at each step. Play/pause, single-step, and
playback-speed controls are included.

The colored radial wind overlay is an **illustrative visualization only**;
it is not a validated wind-field, forecast, or hazard model and must not be
used for operational or property-risk decisions. The browser dashboard binds
to localhost by default and uses no external map or JavaScript services.

### Input

1. **Simulation controls** -- `num_storms`, `seed`, `time_step_hours`
   (6h for V1), `max_duration_days`, `basin` (only `"Atlantic"` is
   supported in V1; anything else raises `ValueError`).
2. **Historical data** -- any DataFrame with at least `storm_id`,
   `timestamp`, `latitude`, `longitude`, `max_wind_kt`. If you're
   starting from the raw NOAA HURDAT2 text file, `load_hurdat2()`
   parses it for you (see "About HURDAT2 data" below); if you already
   have a DataFrame from somewhere else (a database, a CSV a
   colleague produced), just pass it in. The derived transition
   columns (`delta_lat`, `delta_lon`, `delta_wind`) are computed
   automatically -- you don't need to supply them yourself, and it's
   harmless if they're already there.

### Output

`simulate_hurricanes()` returns a `SimulationResult` -- a named tuple
you can either unpack (`tracks, summary = simulate_hurricanes(...)`) or
use by attribute (`result.tracks`, `result.summary`).

**`tracks`** -- one row per simulated storm per time step:

| column | |
|---|---|
| `storm_id`, `step`, `timestamp`, `latitude`, `longitude`, `max_wind_kt` | required minimum |
| `category` | Saffir-Simpson bin: `TD`, `TS`, `1`-`5` |
| `is_over_land` | storm center over land at this step |
| `is_active` | always `True` in V1 -- a storm's track simply ends at lysis, no padded rows |
| `genesis_lat`, `genesis_lon` | broadcast from the storm's first step |
| `max_intensity_kt` | broadcast peak wind reached over the storm's lifetime |

**`summary`** -- one row per simulated storm: `storm_id`, `genesis_time`,
`lysis_time`, `duration_hours`, `genesis_lat`, `genesis_lon`, `min_lat`,
`max_lat`, `max_wind_kt`, `landfall`, `landfall_time`, `landfall_lat`,
`landfall_lon`, `landfall_wind_kt`.

See `hurricane_simulator/schema.py` for the exact, single source of
truth on every column name and constant used throughout the codebase.

## How the Monte Carlo model works (V1)

1. **Genesis** (`genesis.py`) -- bootstrap a historical storm's first
   observation (lat, lon, wind, day-of-year) and jitter it slightly.
   Because it's a bootstrap over real genesis points, historical hot
   spots (Main Development Region, Gulf, Caribbean) show up in roughly
   their historical proportions for free.
2. **Transition** (`transition.py`) -- at each step, find the K most
   similar historical states (a k-d tree over lat/lon/wind/day-of-year)
   and adopt one of their realized `(delta_lat, delta_lon, delta_wind)`
   outcomes, with a little smoothing noise. This is a standard
   nonparametric "analog" / k-NN bootstrap approach: it keeps storms on
   a realistic manifold (typical translation speed, curvature, and
   intensity change for a system of that strength, in that place, at
   that time of year) without hand-coding explicit steering or
   intensity equations.
3. **Land interaction** (`intensity.py` + `landmask.py`) -- once a
   storm's center is over land (checked via the `global-land-mask`
   package, fully offline), its wind decays exponentially toward a low
   residual value instead of following the open-water statistical
   model.
4. **Lysis** -- a track ends when wind drops below 20 kt, the storm
   passes 55°N (treated as extratropical transition), or
   `max_duration_days` is reached.

All of the above are deliberately simple V1 choices, tuned via the
constants at the top of `intensity.py`, `genesis.py`, and
`transition.py` -- adjust them there as the team validates against real
climatology.

## About HURDAT2 data

Two sources of historical data ship with this repo:

- **`tests/fixtures/sample_hurdat2.txt` is synthetic, NOT real NOAA
  data.** It exists purely for fast, deterministic tests --
  `scripts/generate_sample_hurdat2.py` procedurally generates
  plausible (but entirely fictional) storms so the test suite has
  something realistic to load, fit, and assert against without
  depending on a multi-megabyte external file.

- **`data/hurdat2-1851-2025-091226.txt` is the real, current NOAA
  HURDAT2 file** (1,988 storms, 1851 through the end of the 2025
  season), suitable for actually running the simulator against real
  Atlantic climatology:

  ```python
  historical_data = hs.load_hurdat2("data/hurdat2-1851-2025-091226.txt")
  ```

  `www.nhc.noaa.gov` isn't reachable from the sandbox this was built
  in, so this copy was downloaded directly from NOAA in a regular
  browser and dropped into the repo. NOAA republishes this file under
  a new name each time it's updated (the `091226` is a revision
  timestamp, not a date to parse), so when a newer season's data is
  wanted, grab the current file from
  <https://www.nhc.noaa.gov/data/#hurdat> and replace this one --
  `load_hurdat2()` doesn't care about the filename.

  Loading the real file exposed three genuine HURDAT2 data-quality
  quirks that the synthetic fixture never triggered, all now handled
  permanently in the parser rather than worked around:
  - Some pre-1988-ish records use `-99` (not the documented `-999`) as
    a missing-wind sentinel, so `load_hurdat2()` treats *any* negative
    wind or pressure reading as missing rather than matching one
    specific sentinel value.
  - A handful of storms' very first observation is itself missing
    lat/lon/wind, so `sample_genesis()` drops genesis candidates with
    missing fields instead of risking `NaN` propagating into the
    simulation.
  - A couple of individual records in NOAA's own file have literal
    typos -- one 1969 record is missing the comma between its lat and
    lon fields, and one 1975 record is missing its latitude's
    hemisphere letter (`38.83` instead of `38.83N`) -- both recovered
    rather than dropped or misparsed (the 1975 case defaults to `N`
    since every other latitude in the Atlantic file is Northern
    Hemisphere; a bare *longitude*, which would be genuinely
    ambiguous, still raises).

## Project layout

```text
hurricane_simulator/
    __init__.py       public API
    schema.py         column names & tunable constants (single source of truth)
    data.py           HURDAT2 parsing + derived-column prep
    landmask.py        is_over_land()
    intensity.py       category_from_wind(), decay_wind_over_land()
    genesis.py          bootstrap genesis sampler
    transition.py       k-d tree analog/bootstrap transition model
    simulator.py         simulate_hurricanes() -- orchestration
scripts/generate_sample_hurdat2.py   builds the synthetic test fixture
tests/                pytest suite (schema, reproducibility, physical sanity)
examples/run_example.py  end-to-end usage, writes CSVs to output/
    (also writes a visualization PNG; install the visualization extra)
examples/run_dashboard.py  launches the local interactive track predictor
```

## Setup & running tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
python examples/run_example.py
```

To run the example with its visualization output, install
`pip install -e ".[visualization]"` first.
