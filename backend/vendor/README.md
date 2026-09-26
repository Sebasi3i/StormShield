# Vendored third-party code — do not edit

## hurricane_simulator

A copy of the team's hurricane simulator package, taken from
`hurricane_simulator/hurricane_simulator/hurricane_simulator/` as delivered on
2026-09-26, version 0.1.0.

Vendored rather than pip-installed because the package is not published anywhere the
backend can install it from. The tradeoff is explicit: **this is a fork the moment its
author changes anything.** When a new version arrives, re-copy the whole directory
rather than patching files here, and re-run `python -m pytest tests -q`.

Nothing in `app/` may import from here directly except `app/storms.py`, which is the
single adapter between his package and ours. That keeps the blast radius of a re-vendor
to one file.

### What is here

- `hurricane_simulator/` — the Python package, unmodified
- `data/hurdat2-1851-2025-091226.txt` — the HURDAT2 Atlantic record, 1851-2025,
  55,524 observations of 1,988 real storms. His simulator bootstraps from this; it is
  not our data and we do not edit it.

### Dependencies it pulls in

`scipy` (k-d tree for the analog transition model) and `global-land-mask` (landfall
detection), both added to `requirements.txt`. Its `visualization` and `dashboard`
modules import matplotlib and plotly lazily, inside functions, so neither is needed to
generate storms and neither is installed.
