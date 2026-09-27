# Hazus hurricane building loss functions (one-story single-family)

`hazus_sf1_loss_repair.csv.gz` holds FEMA Hazus hurricane loss functions for one-story
single-family homes: every configuration of `W.SF.1` (wood) and `M.SF.1` (masonry),
2,600 rows. Each row gives the building repair cost as a fraction of replacement cost
at peak gust speeds from 50 to 250 mph in 5 mph steps.

## Source

- **Model:** FEMA Hazus Hurricane Model, building damage and loss functions. The library
  files this under "Hazus v5.1 original" and describes it as based on version 4.2 of the
  Hazus Hurricane Model Technical Manual. Method and validation against insurance
  loss data: Vickery et al., "HAZUS-MH Hurricane Model Methodology. II: Damage and Loss
  Estimation", *Natural Hazards Review* 7(2), 2006.
- **Machine-readable copy:** NHERI SimCenter Damage and Loss Model Library,
  <https://github.com/NHERI-SimCenter/DamageAndLossModelLibrary>, commit
  `662999759cc59bf9cfc099d4ff5a5814a7bf01c8` (25 August 2026), file
  `src/dlml/data/hurricane/building/portfolio/Hazus v5.1 original/loss_repair.csv`.
  The library is BSD 3-Clause licensed; its licence is in `LICENSE-DLML`.
- **Extracted:** 27 September 2026. Rows whose ID starts with `W.SF.1.` or `M.SF.1.`,
  unchanged, with the library's plain-language description of each configuration
  (from `loss_repair.json`) added as a second column.

## Columns

| Column | Meaning |
|---|---|
| `ID` | Configuration code plus `-Cost`, e.g. `M.SF.1.gab.tnail.trs.6d.0.0.no.0.null.35-Cost` |
| `Description` | The configuration in words |
| `Demand-Type`, `Demand-Unit` | Peak gust wind speed, mph |
| `DV-Unit` | `loss_ratio`: building repair cost over replacement cost |
| `LossFunction-Theta_0` | `loss ratios | wind speeds`, comma-separated, 41 points each |

Masonry code fields, in order after `M.SF.1`: roof shape (`gab`, `hip`), roof-to-wall
connection (`strap`, `tnail`), roof frame (`trs` wood truss, `ows` open-web steel
joist), roof deck attachment (`6d`, `8d`, `6s`, `8s`, `std`, `sup`), shutters (`0`/`1`),
secondary water resistance (`0`/`1`/`null`), garage (`no`, `std`, `wkd`, `sup`, `null`),
masonry reinforcing (`0`/`1`/`null`), roof cover (`cshl`, `smtl`, `null`), terrain
roughness in cm (`3`, `15`, `35`, `70`, `100`). The descriptions spell each one out.

## Wind basis

Hazus defines these functions on the peak gust in **open terrain** at 10 m; the terrain
field describes the building's surroundings, which Hazus applies inside the function.
That is the same metric the platform's wind step produces
(`peak_3s_gust_10m_open_terrain_mph`), so the platform's gust goes in without conversion
and the terrain field should describe the home's neighbourhood, not the wind.

## Scope

Building structure only: contents and loss of use are separate Hazus functions and are
not included. Vickery et al. note the functions tend to understate small losses below
about 100 mph, where fallen trees (not modelled) cause much of the damage.

Used by `backend/scripts/build_damage_curves.py`, which writes
`backend/app/fixtures/damage_curves.json`.
