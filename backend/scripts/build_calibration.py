"""Build every wind-calibration artifact in one dependency-correct pass, and promote
them together or not at all.

Each of fit_gust_factor.py, calibrate_wind_field.py, validate_wind_field.py and
compare_calibration_cohorts.py is independently callable on its own - this exists only
to make the recommended build sequence (docs/calibration.md; originally the
implementation spec's section 9) a single command, and to let every step share the
expensive wind-field computations (calibrate_wind_field.all_unit_gust_tables, the raw
gust pairs frame) instead of recomputing them per cohort:

  1. Fit the primary gust artifact and the primary profile (with its storm holdouts),
     checking the profile fit used exactly that gust artifact's own factor.
  2. Do the same for the combined cohort, as an explicit alternative - never promoted
     to gust_factor_model.json / wind_calibration.json, only used for validation's
     combined_sensitivity section and the sensitivity/payout comparison.
  3. Validate the primary bundle (fitted and holdout) and evaluate it, frozen, on the
     legacy storms it has never seen.
  4. Build the sensitivity and payout comparison across all three scenarios, capturing
     the CURRENT (pre-promotion) production bundle first as a labeled benchmark -
     captured here, before anything below is written, never after.
  5. Check every artifact is finite, strict-JSON-safe, and internally consistent (the
     gust factor a profile fit reports matches the gust artifact it was built from).
  6. Promote gust_factor_model.json, wind_calibration.json, wind_validation.json and
     calibration_sensitivity.json together, each via a write-then-rename so a crash
     mid-build cannot leave a truncated fixture, then regenerate
     sample_storm_losses_response.json in a fresh subprocess (GUST_FACTOR and the
     fixture loaders are cached at import/first call in THIS process, so reusing it
     would regenerate the sample against stale values even after the files on disk
     are correct).

Nothing here is promoted if any step raises - build() must complete before promote()
is called, and main() calls them in that order.

Usage, from the backend directory:

    python scripts/build_calibration.py
    python scripts/build_calibration.py --no-promote      # build and validate only
    python scripts/build_calibration.py --no-sample-response
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import calibrate_wind_field as cwf  # noqa: E402
import calibration_common as cc  # noqa: E402
import compare_calibration_cohorts as ccc  # noqa: E402
import fit_gust_factor  # noqa: E402
import validate_wind_field as vwf  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES_DIR = BACKEND / "app" / "fixtures"
SAMPLE_RESPONSE = FIXTURES_DIR / "sample_storm_losses_response.json"

PROMOTED_ARTIFACTS = (
    "gust_factor_model.json",
    "wind_calibration.json",
    "wind_validation.json",
    "calibration_sensitivity.json",
)


def _check_finite(name: str, value: object) -> None:
    """Strict JSON has no NaN or Infinity; every artifact here is walked before it is
    written, so a bad value is a build failure, not a downstream 500 or a silently
    wrong chart."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise cc.DataValidationError(f"{name} is not finite: {value}")
    if isinstance(value, dict):
        for key, v in value.items():
            _check_finite(f"{name}.{key}", v)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_finite(f"{name}[{i}]", v)


def build(*, include_current_production: bool = True) -> dict:
    """Every artifact, in memory, nothing written. Raises on any inconsistency rather
    than returning a partial result, so a caller cannot promote a build that didn't
    fully succeed."""
    # Captured first, before any of this build's own results exist, so it is
    # genuinely the pre-change benchmark even if this same run promotes new values
    # afterwards - see compare_calibration_cohorts.py's module docstring.
    current_production_bundle = vwf.production_bundle() if include_current_production else None

    tables = cwf.all_unit_gust_tables()
    pairs = pd.read_csv(fit_gust_factor.PAIRS)

    gust_primary = fit_gust_factor.build_model(cohort=cc.PRIMARY_COHORT)
    gust_combined = fit_gust_factor.build_model(cohort=cc.COMBINED_COHORT)

    calibration_primary = cwf.calibrate(cc.PRIMARY_COHORT, tables=tables, pairs=pairs)
    calibration_combined = cwf.calibrate(cc.COMBINED_COHORT, tables=tables, pairs=pairs)

    # The profile fit computes its own gust factor via fit_gust_factor.build_model
    # internally; this checks it is exactly the artifact built for that cohort above,
    # not a coincidentally-equal number - see calibrate_wind_field.py's module
    # docstring: "Do not rely on a production fixture to decide which factor an
    # alternative fit uses."
    for label, calibration, gust_model in (
        ("primary", calibration_primary, gust_primary),
        ("combined", calibration_combined, gust_combined),
    ):
        if calibration["gust_factor"] != gust_model["gust_factor"]:
            raise cc.DataValidationError(
                f"{label} profile fit's gust factor ({calibration['gust_factor']}) does "
                f"not match the {label} gust artifact built alongside it "
                f"({gust_model['gust_factor']})"
            )
        if calibration["gust_factor_model_id"] != gust_model["gust_factor_model_id"]:
            raise cc.DataValidationError(
                f"{label} profile fit references gust model id "
                f"{calibration['gust_factor_model_id']!r}, built artifact is "
                f"{gust_model['gust_factor_model_id']!r}"
            )

    validation = vwf.build_validation_report(
        primary_calibration=calibration_primary, combined_calibration=calibration_combined
    )
    sensitivity = ccc.build_comparison(
        tables=tables,
        pairs=pairs,
        primary_calibration=calibration_primary,
        combined_calibration=calibration_combined,
        current_production_bundle=current_production_bundle,
    )

    artifacts = {
        "gust_factor_model.json": gust_primary,
        "wind_calibration.json": calibration_primary,
        "wind_validation.json": validation,
        "calibration_sensitivity.json": sensitivity,
    }
    for name, artifact in artifacts.items():
        _check_finite(name, artifact)

    return {
        "artifacts": artifacts,
        # Not promoted to any production fixture - recorded only so a caller can
        # print or inspect what the combined alternative looked like in this build.
        "combined_gust_factor_model": gust_combined,
        "combined_calibration": calibration_combined,
    }


def _write_atomically(path: Path, payload: dict) -> None:
    text = json.dumps(payload, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def promote(built: dict) -> None:
    """Write every artifact build() produced, each via write-then-rename. build()
    already validated the whole set before this is called, so there is nothing left
    to check here - only to write."""
    for name, artifact in built["artifacts"].items():
        _write_atomically(FIXTURES_DIR / name, artifact)


def regenerate_sample_response() -> None:
    """Exactly the one-liner tests/test_storm_losses_api.py's own docstring gives for
    this, run as a fresh subprocess rather than inline: GUST_FACTOR and the fixture
    loaders' lru_cache in THIS process were populated before promote() overwrote the
    files on disk, and would still hand back the old values here."""
    snippet = (
        "import json, pathlib\n"
        "from starlette.testclient import TestClient\n"
        "from app.main import app\n"
        "pathlib.Path('app/fixtures/sample_storm_losses_response.json').write_text(\n"
        "    json.dumps(TestClient(app).get('/api/v1/storm-losses/example').json(), indent=2) + chr(10),\n"
        "    encoding='utf-8',\n"
        ")\n"
    )
    subprocess.run([sys.executable, "-c", snippet], cwd=str(BACKEND), check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-promote", action="store_true", help="build and validate only; write nothing")
    parser.add_argument(
        "--no-current-production",
        action="store_true",
        help="omit the pre-build production bundle from the sensitivity comparison",
    )
    parser.add_argument(
        "--no-sample-response", action="store_true", help="skip regenerating sample_storm_losses_response.json"
    )
    args = parser.parse_args(argv)

    built = build(include_current_production=not args.no_current_production)

    print("built (validated, not yet written):")
    gp, cp = built["artifacts"]["gust_factor_model.json"], built["artifacts"]["wind_calibration.json"]
    print(f"  primary:  gust {gp['gust_factor']}, decay {cp['outer_decay_exponent']}, land {cp['land_exposure_factor']}, "
          f"{cp['data']['station_storm_pairs']} pairs from {cp['data']['storms']} storms")
    gc, cc_ = built["combined_gust_factor_model"], built["combined_calibration"]
    print(f"  combined (not promoted): gust {gc['gust_factor']}, decay {cc_['outer_decay_exponent']}, "
          f"land {cc_['land_exposure_factor']}, {cc_['data']['station_storm_pairs']} pairs from {cc_['data']['storms']} storms")
    validation = built["artifacts"]["wind_validation.json"]
    print(f"  validation sections: {[k for k in validation if k.endswith(('fitted', 'holdout', 'primary', 'sensitivity'))]}")
    sensitivity = built["artifacts"]["calibration_sensitivity.json"]
    print(f"  sensitivity scenarios: {list(sensitivity['scenarios'])}")

    if args.no_promote:
        print("\n--no-promote: nothing written")
        return 0

    promote(built)
    print(f"\nwrote {', '.join(built['artifacts'])} to {FIXTURES_DIR}")

    if not args.no_sample_response:
        regenerate_sample_response()
        print(f"wrote {SAMPLE_RESPONSE.relative_to(BACKEND)}")

    print(
        "\nRestart the backend process to pick up these fixtures: GUST_FACTOR and the "
        "fixture loaders in app/wind.py are cached at import/first call and will not "
        "see these files change underneath a running process."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
