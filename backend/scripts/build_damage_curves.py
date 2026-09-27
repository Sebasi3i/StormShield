"""Build the platform's damage curves from the FEMA Hazus hurricane loss functions.

The claims engine turns a property's peak gust into a damage fraction of replacement
cost through five curves: two vulnerability classes (homes built before and after the
2002 Florida Building Code), each with a baseline and the upgrades that can be funded
(shutters for both, roof-to-wall straps for the older homes only, since the 2002 code
already requires them). Until now those curves were assumed. This script derives them
from Hazus, whose building loss functions are indexed by the same features the upgrades
change, and writes backend/app/fixtures/damage_curves.json.

Input: data/calibration/hazus_hurricane_loss/hazus_sf1_loss_repair.csv.gz, the Hazus
one-story single-family rows from the NHERI SimCenter Damage and Loss Model Library
(see that folder's README for provenance).

What maps to what (every choice is recorded in the fixture):

  - Wind: Hazus is defined on the open-terrain peak gust at 10 m, the metric the
    platform's wind step produces, so no conversion. The Hazus terrain field describes
    the home's surroundings; suburban roughness (0.35 m) is used for every home.
  - Building: one-story masonry single-family (M.SF.1) with a wood-truss roof, no
    garage, no masonry reinforcing (it moves these curves by under a point), no
    information on roof cover.
  - Pre-2002 code: roof-to-wall toe-nails, 6d roof-deck nails, no secondary water
    resistance, no shutters.
  - Post-2002 code: roof-to-wall straps, 8d roof-deck nails, secondary water
    resistance, no shutters.
  - Upgrades change one feature of their class: shutters on, or toe-nails to straps.
  - Roof shape: Hazus separates gable and hip roofs, and a hip roof loses about half as
    much as a gable roof at 140 mph. Rather than picking one mix, this script publishes
    all three: the gable curve, the hip curve, and their mean ("blended"). A property
    that declares its own roof shape gets the matching curve; a property that does not
    gets blended, the same equal-weight assumption the platform used before roof shape
    existed as a field, and still no source for Florida's actual gable/hip mix.

A few Hazus curves dip by a fraction of a percent as wind rises; the engine refuses a
decreasing curve, so every curve (gable, hip and blended alike) is made nondecreasing
with a running maximum. Below about 105 mph some also cross by a hundredth of a percent,
which would show as an upgrade adding damage, so within each roof shape every upgrade is
capped at its own baseline and the post-2002 baseline at the pre-2002 one. The largest of
each adjustment is recorded.

    python scripts/build_damage_curves.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
SOURCE = BACKEND.parent / "data" / "calibration" / "hazus_hurricane_loss" / "hazus_sf1_loss_repair.csv.gz"
FIXTURE = BACKEND / "app" / "fixtures" / "damage_curves.json"

WIND_METRIC = "peak_3s_gust_10m_open_terrain_mph"
TERRAIN_CM = "35"

# Hazus source codes for the two roof shapes it separates, and the platform's own
# roof_shape values, in the order curves are built and published for each config.
HAZUS_ROOF_CODE = {"gable": "gab", "hip": "hip"}
OUTPUT_ROOF_SHAPES = ("gable", "hip", "blended")

# (class, upgrade) -> Hazus M.SF.1 fields after the roof shape:
#   roof-to-wall, roof frame, deck attachment, shutters, secondary water resistance,
#   garage, masonry reinforcing, roof cover
CONFIGS: dict[tuple[str, str], dict] = {
    ("pre_fbc_2002", "baseline"): dict(rwc="tnail", deck="6d", shutters="0", swr="0"),
    ("pre_fbc_2002", "shutters"): dict(rwc="tnail", deck="6d", shutters="1", swr="0"),
    ("pre_fbc_2002", "roof_straps"): dict(rwc="strap", deck="6d", shutters="0", swr="0"),
    ("post_fbc_2002", "baseline"): dict(rwc="strap", deck="8d", shutters="0", swr="1"),
    ("post_fbc_2002", "shutters"): dict(rwc="strap", deck="8d", shutters="1", swr="1"),
}

CLASS_TEXT = {
    "pre_fbc_2002": "Single-family home built before the 2002 Florida Building Code",
    "post_fbc_2002": "Single-family home built to the 2002 Florida Building Code or later",
}
UPGRADE_TEXT = {
    "baseline": "as built",
    "shutters": "with shutters added",
    "roof_straps": "with roof-to-wall straps replacing toe-nails",
}


def hazus_id(roof: str, cfg: dict) -> str:
    return (
        f"M.SF.1.{roof}.{cfg['rwc']}.trs.{cfg['deck']}.{cfg['shutters']}.{cfg['swr']}"
        f".no.0.null.{TERRAIN_CM}"
    )


def load_source(path: Path = SOURCE) -> dict[str, tuple[np.ndarray, np.ndarray, str]]:
    table = pd.read_csv(path)
    out = {}
    for hid, description, function in zip(table["ID"], table["Description"], table["LossFunction-Theta_0"]):
        losses, winds = function.split("|")
        out[hid.removesuffix("-Cost")] = (
            np.array(winds.split(","), float), np.array(losses.split(","), float), description,
        )
    return out


def build_curve_set(path: Path = SOURCE) -> dict:
    source = load_source(path)
    winds_ref = None
    hazus_ids: dict[tuple[str, str], dict[str, str]] = {}
    raw: dict[tuple[tuple[str, str], str], np.ndarray] = {}
    for key, cfg in CONFIGS.items():
        ids = {shape: hazus_id(code, cfg) for shape, code in HAZUS_ROOF_CODE.items()}
        missing = [i for i in ids.values() if i not in source]
        if missing:
            raise KeyError(f"Hazus configuration not in the source table: {missing}")
        for i in ids.values():
            if winds_ref is None:
                winds_ref = source[i][0]
            if not np.array_equal(source[i][0], winds_ref):
                raise ValueError(f"{i} is on a different wind grid")
        hazus_ids[key] = ids
        raw[(key, "gable")] = source[ids["gable"]][1]
        raw[(key, "hip")] = source[ids["hip"]][1]
        raw[(key, "blended")] = np.mean([source[ids["gable"]][1], source[ids["hip"]][1]], axis=0)

    # A few Hazus curves dip by a fraction of a percent as wind rises; the engine
    # refuses a decreasing curve, so every curve is made nondecreasing on its own.
    # Recorded per curve (not just the aggregate largest), with the wind speed the
    # biggest adjustment happened at, so "where does this happen" has an answer.
    monotone_adjustments: list[dict] = []
    values: dict[tuple[tuple[str, str], str], np.ndarray] = {}
    for entry_key, arr in raw.items():
        monotone = np.maximum.accumulate(arr)
        fix = monotone - arr
        idx = int(np.argmax(fix))
        (vclass, upgrade), shape = entry_key
        monotone_adjustments.append(
            {
                "vulnerability_class": vclass,
                "upgrade_id": upgrade,
                "roof_shape": shape,
                "wind_mph": float(winds_ref[idx]),
                "adjustment": round(float(fix[idx]), 4),
            }
        )
        values[entry_key] = monotone
    monotone_adjustments.sort(key=lambda a: a["adjustment"], reverse=True)
    largest_monotone_fix = monotone_adjustments[0]["adjustment"] if monotone_adjustments else 0.0

    # The source is noisy at low speeds: within each roof shape, cap the post-2002
    # baseline at the pre-2002 one, and each upgrade at its own class baseline. The
    # minimum of two nondecreasing curves is nondecreasing, so the curves stay valid.
    # Recorded per curve for the same reason as the monotone fix above.
    ordering_adjustments: list[dict] = []
    order = [(("post_fbc_2002", "baseline"), ("pre_fbc_2002", "baseline"))] + [
        (key, (key[0], "baseline")) for key in CONFIGS if key[1] != "baseline"
    ]
    for shape in OUTPUT_ROOF_SHAPES:
        for key, ceiling in order:
            capped = np.minimum(values[(key, shape)], values[(ceiling, shape)])
            fix = values[(key, shape)] - capped
            idx = int(np.argmax(fix))
            vclass, upgrade = key
            ordering_adjustments.append(
                {
                    "vulnerability_class": vclass,
                    "upgrade_id": upgrade,
                    "roof_shape": shape,
                    "capped_to": f"{ceiling[0]}.{ceiling[1]}",
                    "wind_mph": float(winds_ref[idx]),
                    "adjustment": round(float(fix[idx]), 4),
                }
            )
            values[(key, shape)] = capped
    ordering_adjustments.sort(key=lambda a: a["adjustment"], reverse=True)
    largest_order_fix = ordering_adjustments[0]["adjustment"] if ordering_adjustments else 0.0

    def source_note(vclass: str, upgrade: str, shape: str) -> str:
        base = f"FEMA Hazus hurricane building loss function. {CLASS_TEXT[vclass]}, {UPGRADE_TEXT[upgrade]}"
        ids = hazus_ids[(vclass, upgrade)]
        if shape == "blended":
            gable_description = source[ids["gable"]][2].replace("Gable roof. ", "")
            return f"{base}: mean of Hazus {ids['gable']} and {ids['hip']} (gable and hip roof). {gable_description}"
        return f"{base}, {shape} roof: Hazus {ids[shape]}. {source[ids[shape]][2]}"

    curves = []
    for (vclass, upgrade) in CONFIGS:
        for shape in OUTPUT_ROOF_SHAPES:
            points = [[0, 0.0]] + [
                [int(w) if float(w).is_integer() else float(w), round(float(v), 4)]
                for w, v in zip(winds_ref, values[((vclass, upgrade), shape)])
            ]
            # The first Hazus points are exactly zero; drop repeats so the curve starts
            # (0, 0) and then rises from the last zero speed.
            while len(points) > 2 and points[1][1] == 0.0 and points[2][1] == 0.0:
                points.pop(1)
            curves.append(
                {
                    "curve_id": f"{vclass}.{upgrade}.{shape}.hazus.v1",
                    "vulnerability_class": vclass,
                    "upgrade_id": upgrade,
                    "roof_shape": shape,
                    "wind_metric": WIND_METRIC,
                    "evidence_status": "sourced",
                    "source_note": source_note(vclass, upgrade, shape),
                    "points": points,
                }
            )

    return {
        "curve_set_id": "hazus-msf1-suburban-v2",
        "wind_metric": WIND_METRIC,
        "evidence_status": "sourced",
        "upper_supported_wind_mph": int(winds_ref.max()),
        "provenance": {
            "summary": (
                "Building loss as a fraction of replacement cost, from the FEMA Hazus "
                "hurricane model's published loss functions for one-story masonry "
                "single-family homes. Sourced: every point is a Hazus value or the mean of "
                "two. The mapping from the platform's classes to Hazus building features "
                "is a modelling choice, recorded below."
            ),
            "roof_shape_variants": list(OUTPUT_ROOF_SHAPES),
            "version_note": (
                "v2 (27 September 2026): every curve split into gable, hip and blended "
                "variants (5 -> 15 curves) and roof_shape added as a field; v1 published "
                "only the single blended-equivalent curve per class/upgrade. See "
                "roof_shape below."
            ),
            "source": (
                "FEMA Hazus Hurricane Model building loss functions (Technical Manual v4.2), "
                "machine-readable copy from the NHERI SimCenter Damage and Loss Model "
                "Library (BSD 3-Clause), commit 662999759cc59bf9cfc099d4ff5a5814a7bf01c8. "
                "Method and validation against insurance losses: Vickery et al. 2006, "
                "Natural Hazards Review 7(2). Extract and provenance: "
                "data/calibration/hazus_hurricane_loss."
            ),
            "wind_basis": (
                "Hazus loss functions take the open-terrain peak gust at 10 m, the metric "
                "the wind step produces; no conversion is applied. The home's surroundings "
                "enter through the Hazus terrain field, set to suburban (z0 = 0.35 m) for "
                "every home."
            ),
            "class_mapping": {
                "building": "Hazus M.SF.1: one-story masonry, wood-truss roof, no garage, no masonry reinforcing, roof cover unknown",
                "pre_fbc_2002": "roof-to-wall toe-nails, 6d roof-deck nails, no secondary water resistance, no shutters",
                "post_fbc_2002": "roof-to-wall straps, 8d roof-deck nails, secondary water resistance, no shutters",
                "shutters": "same class with Hazus shutters on",
                "roof_straps": "pre_fbc_2002 with straps in place of toe-nails; not offered for post_fbc_2002, which already has them",
            },
            "roof_shape": (
                "Published separately: the Hazus gable curve, the Hazus hip curve, and "
                "their mean ('blended') at each speed. A hip roof loses about half as much "
                "as a gable roof at 140 mph. A property that declares its own roof_shape "
                "('gable' or 'hip') gets the matching curve; without one it gets 'blended', "
                "an equal-weight assumption with no source for Florida's actual gable/hip "
                "mix. That fallback, not the split itself, is the largest remaining "
                "assumption in these curves - which roof shape an unlabeled Florida home "
                "actually has is a separate, larger open question."
            ),
            "monotone_adjustment": (
                "Every curve (gable, hip and blended alike) made nondecreasing with a "
                f"running maximum; the largest adjustment was {largest_monotone_fix:.4f} of "
                "replacement cost, on curve "
                f"{monotone_adjustments[0]['vulnerability_class']}.{monotone_adjustments[0]['upgrade_id']}."
                f"{monotone_adjustments[0]['roof_shape']} at {monotone_adjustments[0]['wind_mph']:g} mph. "
                "Every curve's own adjustment, and where on its wind axis it happens, is in "
                "monotone_adjustment_detail below, largest first."
            ),
            "monotone_adjustment_detail": monotone_adjustments,
            "ordering_adjustment": (
                "Within each roof shape, every upgrade capped at its class baseline, and "
                "the post-2002 baseline at the pre-2002 one, so an upgrade never adds "
                f"damage; the largest cap was {largest_order_fix:.4f} of replacement cost, "
                "where the source curves cross by simulation noise below 105 mph, on curve "
                f"{ordering_adjustments[0]['vulnerability_class']}.{ordering_adjustments[0]['upgrade_id']}."
                f"{ordering_adjustments[0]['roof_shape']} at {ordering_adjustments[0]['wind_mph']:g} mph "
                f"(capped to {ordering_adjustments[0]['capped_to']}). Every curve's own cap is in "
                "ordering_adjustment_detail below, largest first."
            ),
            "ordering_adjustment_detail": ordering_adjustments,
            "scope": (
                "Building structure only; contents and loss of use are separate Hazus "
                "functions and are not included. Hazus tends to understate small losses "
                "below about 100 mph, where fallen trees (not modelled) do much of the "
                "damage."
            ),
            "not_validated_here": (
                "Hazus was validated by its authors against insurance losses from past "
                "storms; these curves have not been checked against this portfolio's own "
                "claims. The Finance workbook's upgrade damage-effect request is still "
                "unanswered."
            ),
            "script": "backend/scripts/build_damage_curves.py",
        },
        "curves": curves,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", nargs="?", default=str(SOURCE))
    parser.add_argument("--fixture", default=str(FIXTURE))
    args = parser.parse_args(argv)

    curve_set = build_curve_set(Path(args.source))
    Path(args.fixture).write_text(json.dumps(curve_set, indent=2) + "\n", encoding="utf-8")

    grid = (105, 120, 140, 160, 180)
    print("mph".ljust(38), *grid)
    for curve in curve_set["curves"]:
        winds, losses = zip(*curve["points"])
        label = f"{curve['vulnerability_class']} {curve['upgrade_id']} {curve['roof_shape']}"
        print(f"{label:38s}", *[f"{np.interp(g, winds, losses):.3f}" for g in grid])
    print(curve_set["provenance"]["monotone_adjustment"])
    print(curve_set["provenance"]["ordering_adjustment"])
    print(f"wrote {args.fixture}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
