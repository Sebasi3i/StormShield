"""Fit the storm-size model from the NOAA HURDAT2 wind radii.

The wind field needs, per storm, a radius of maximum wind (RMW), an outer decay
exponent and the distances over which the wind tapers to nothing. The hurricane
simulator publishes none of these, so until now every storm was priced with one
demonstration set (30 km, 0.5, 200-300 km). This script replaces that constant with a
relation fitted to the observed record:

  - HURDAT2 records the RMW for every fix from 2021 on, and the maximum extent of
    34, 50 and 64 kt winds in each quadrant from 2004 on. The file is the one bundled
    with the hurricane_simulator package, so the fit needs no download.
  - RMW is fitted as ln(rmw_km) = a + b * max_wind_kt + c * latitude on hurricane-
    strength fixes, the form used in the operational literature (a smaller eye for a
    stronger, lower-latitude storm).
  - The outer decay exponent x of the profile V(r) = Vmax * (rmw / r) ** x is solved
    per fix from the 64 kt radius: x = ln(Vmax / 64) / ln(R64 / rmw), and the median
    is taken.
  - Taper start and cutoff are taken from the distribution of the 34 kt radius: the
    median and the 90th percentile of the largest quadrant.

The result is written to app/fixtures/storm_size_model.json with its provenance and
fit statistics, and the extracted radii table to data/processed/hurdat2_wind_radii.csv
so the fit can be inspected. Parameters are constant within each event: the model is
evaluated once per storm, at its peak-intensity fix.

Usage, from the backend directory:

    python scripts/fit_storm_size.py            # bundled record
    python scripts/fit_storm_size.py <hurdat2.txt>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
FIXTURE = BACKEND / "app" / "fixtures" / "storm_size_model.json"
PROCESSED = BACKEND.parent / "data" / "processed" / "hurdat2_wind_radii.csv"

NAUTICAL_MILE_KM = 1.852
MISSING = -999.0

# Hurricane strength, HURDAT2 convention (one-minute sustained, knots).
HURRICANE_KT = 64.0
# First season with the RMW column populated throughout.
RMW_FROM_YEAR = 2021
# First season with wind radii recorded.
RADII_FROM_YEAR = 2004

# The fitted RMW is clamped to the range the record itself spans for hurricanes, so
# an extreme extrapolation cannot produce a 2 km or 300 km eye.
RMW_BOUNDS_KM = (8.0, 80.0)


def bundled_hurdat2() -> Path:
    import hurricane_simulator

    resources = Path(hurricane_simulator.__file__).resolve().parent / "resources"
    files = sorted(resources.glob("hurdat2-*.txt"))
    if not files:
        raise FileNotFoundError(f"no hurdat2-*.txt in {resources}")
    return files[-1]


def _number(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        return math.nan
    return math.nan if value == MISSING else value


def _quadrant_max(fields: list[str]) -> float:
    values = [_number(field) for field in fields]
    values = [value for value in values if not math.isnan(value) and value > 0]
    return max(values) if values else math.nan


def read_wind_radii(path: Path) -> pd.DataFrame:
    """Every HURDAT2 fix with its intensity, position and wind radii, in km.

    Radii are the largest of the four quadrants: the profile is axisymmetric, so the
    largest quadrant is the extent the model must reach to match the observed storm.
    """
    rows = []
    storm_id = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 4:
                continue
            if parts[0][:2] in ("AL", "EP", "CP") and parts[0][2:].isdigit():
                storm_id = parts[0]
                continue
            if len(parts) < 21:
                continue
            latitude = float(parts[4][:-1]) * (1 if parts[4].endswith("N") else -1)
            rows.append(
                {
                    "storm_id": storm_id,
                    "timestamp": datetime.strptime(parts[0] + parts[1], "%Y%m%d%H%M"),
                    "record_identifier": parts[2],
                    "status": parts[3],
                    "latitude": latitude,
                    "max_wind_kt": _number(parts[6]),
                    "r34_km": _quadrant_max(parts[8:12]) * NAUTICAL_MILE_KM,
                    "r50_km": _quadrant_max(parts[12:16]) * NAUTICAL_MILE_KM,
                    "r64_km": _quadrant_max(parts[16:20]) * NAUTICAL_MILE_KM,
                    "rmw_km": _number(parts[20]) * NAUTICAL_MILE_KM,
                }
            )
    table = pd.DataFrame(rows)
    table["year"] = table["timestamp"].dt.year
    return table


def fit(table: pd.DataFrame) -> dict:
    hurricanes = table[(table["status"] == "HU") & (table["max_wind_kt"] >= HURRICANE_KT)]

    # --- RMW against intensity and latitude ---------------------------------------
    with_rmw = hurricanes[(hurricanes["rmw_km"] > 0) & (hurricanes["year"] >= RMW_FROM_YEAR)]
    design = np.column_stack(
        [np.ones(len(with_rmw)), with_rmw["max_wind_kt"], with_rmw["latitude"]]
    )
    target = np.log(with_rmw["rmw_km"].to_numpy())
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    predicted = design @ coefficients
    residual = target - predicted
    r_squared = 1.0 - residual.var() / target.var()

    # --- Outer decay exponent from the 64 kt radius ---------------------------------
    usable = with_rmw[
        (with_rmw["r64_km"] > with_rmw["rmw_km"]) & (with_rmw["max_wind_kt"] > HURRICANE_KT)
    ]
    exponents = np.log(usable["max_wind_kt"] / HURRICANE_KT) / np.log(
        usable["r64_km"] / usable["rmw_km"]
    )

    # --- Outer extent from the 34 kt radius ------------------------------------------
    outer = hurricanes[(hurricanes["r34_km"] > 0) & (hurricanes["year"] >= RADII_FROM_YEAR)]["r34_km"]

    return {
        "rmw": {
            "form": "ln(rmw_km) = intercept + per_kt * max_wind_kt + per_degree_latitude * latitude",
            "intercept": float(coefficients[0]),
            "per_kt": float(coefficients[1]),
            "per_degree_latitude": float(coefficients[2]),
            "bounds_km": list(RMW_BOUNDS_KM),
            "fixes": int(len(with_rmw)),
            "storms": int(with_rmw["storm_id"].nunique()),
            "seasons": [int(with_rmw["year"].min()), int(with_rmw["year"].max())],
            "r_squared": round(float(r_squared), 3),
            "residual_sd_ln": round(float(residual.std()), 3),
        },
        "outer_decay_exponent": {
            "value": round(float(exponents.median()), 3),
            "interquartile": [round(float(exponents.quantile(0.25)), 3), round(float(exponents.quantile(0.75)), 3)],
            "fixes": int(len(exponents)),
            "method": "median of ln(max_wind_kt / 64) / ln(r64_km / rmw_km) over fixes with both radii",
        },
        "taper": {
            "taper_start_km": round(float(outer.median()), 0),
            "cutoff_km": round(float(outer.quantile(0.90)), 0),
            "fixes": int(len(outer)),
            "method": "median and 90th percentile of the largest-quadrant 34 kt radius of hurricane fixes",
        },
    }


def rmw_km(model: dict, max_wind_kt: float, latitude: float) -> float:
    """The model's RMW for a storm of this intensity at this latitude, clamped."""
    rmw = model["rmw"]
    value = math.exp(
        rmw["intercept"] + rmw["per_kt"] * max_wind_kt + rmw["per_degree_latitude"] * latitude
    )
    low, high = rmw["bounds_km"]
    return min(high, max(low, value))


def build_model(path: Path) -> tuple[dict, pd.DataFrame]:
    table = read_wind_radii(path)
    fitted = fit(table)
    model = {
        "storm_size_model_id": "hurdat2-radii-fit-v1",
        "evidence_status": "sourced",
        "validation_status": "not validated against station observations",
        "source": {
            "dataset": "NOAA NHC HURDAT2 Atlantic best track",
            "file": path.name,
            "note": (
                "Bundled with the hurricane_simulator package. Wind radii (34/50/64 kt, "
                f"per quadrant) recorded from {RADII_FROM_YEAR}; radius of maximum wind "
                f"recorded from {RMW_FROM_YEAR}. Radii are best-track estimates, largest "
                "quadrant, converted from nautical miles."
            ),
        },
        "scope": (
            "Parameters are constant within each event: evaluated once per storm at its "
            "peak-intensity track point. The profile is axisymmetric; forward-motion "
            "asymmetry and land effects are not represented."
        ),
        "fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "backend/scripts/fit_storm_size.py",
        **fitted,
        "examples_km": {
            f"{kt} kt at {lat} N": round(rmw_km({"rmw": fitted["rmw"]}, kt, lat), 1)
            for kt, lat in ((64, 25.0), (96, 26.0), (120, 26.0), (140, 27.0), (155, 20.0))
        },
    }
    return model, table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("hurdat2", nargs="?", help="HURDAT2 text file; default: the bundled one")
    parser.add_argument("--fixture", default=str(FIXTURE))
    parser.add_argument("--csv", default=str(PROCESSED))
    args = parser.parse_args(argv)

    path = Path(args.hurdat2) if args.hurdat2 else bundled_hurdat2()
    model, table = build_model(path)

    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.csv, index=False)
    Path(args.fixture).write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")

    rmw = model["rmw"]
    print(f"read {len(table)} fixes from {path.name}; wrote {args.csv}")
    print(
        f"RMW: ln(km) = {rmw['intercept']:.3f} {rmw['per_kt']:+.4f} kt {rmw['per_degree_latitude']:+.4f} lat "
        f"on {rmw['fixes']} hurricane fixes / {rmw['storms']} storms, R^2 {rmw['r_squared']}"
    )
    print(f"decay exponent {model['outer_decay_exponent']['value']} (IQR {model['outer_decay_exponent']['interquartile']})")
    print(f"taper {model['taper']['taper_start_km']:.0f} km, cutoff {model['taper']['cutoff_km']:.0f} km")
    print("examples:", model["examples_km"])
    print(f"wrote {args.fixture}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
