"""Give the demo portfolio's ten properties an illustrative roof_shape.

Not real data, and not meant to become real data by another name. The demo
portfolio's coordinates are downtown/commercial buildings (verified against FDOT
aerial imagery), not the single-family homes Hazus's M.SF.1 curves model, so running
an actual roof classifier against them would misrepresent both the model and the
classifier. Until real per-home roof shape exists for a real portfolio, this script
assigns each demo property "gable" or "hip" with a fixed seed, purely so the new
gable/hip curve selection in app/claims.py has something to exercise end to end. The
portfolio's missing_input note is updated to say so, the same way vulnerability_class
already is flagged as an assigned placeholder rather than measured.

Seeded and reproducible: same seed, same property_id order, same assignment every run
- see tests/test_storm_losses_api.py::test_demo_roof_shapes_are_reproducible_from_the_seed.

    python scripts/assign_demo_roof_shapes.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "app" / "fixtures"
PORTFOLIO = FIXTURES / "example_portfolio.json"

# 42, like every other demo seed in this codebase (GenerateStormsRequest,
# GenerateFloridaStormsRequest). Change deliberately, never casually - it would
# silently reshuffle which demo properties are "gable" and which are "hip".
SEED = 42
ROOF_SHAPES = ("gable", "hip")

MISSING_INPUT_ROOF_SHAPE_NOTE = (
    " roof_shape is likewise NOT real data: assigned by "
    "scripts/assign_demo_roof_shapes.py with a fixed seed, purely so the platform's "
    "gable/hip curve selection has a mix of both to exercise. It is not a "
    "classification of these addresses, which are commercial/downtown buildings, not "
    "the single-family homes the Hazus curves model - see docs/calibration.md section 7."
)


def assign_roof_shapes(properties: list[dict], seed: int = SEED) -> dict[str, str]:
    """One roof shape per property_id, keyed and drawn in property_id order.

    Sorting by property_id rather than trusting list order keeps the assignment
    stable even if the portfolio's properties are ever reordered or re-sorted.
    """
    rng = random.Random(seed)
    return {
        prop["property_id"]: rng.choice(ROOF_SHAPES)
        for prop in sorted(properties, key=lambda p: p["property_id"])
    }


def render_portfolio(portfolio: dict) -> str:
    """Rewrite the fixture with the file's existing style: one compact line per
    property, so a diff shows exactly what changed rather than reformatting
    everything."""
    lines = ["{"]
    for key in portfolio:
        if key == "properties":
            continue
        lines.append(f"  {json.dumps(key)}: {json.dumps(portfolio[key])},")
    lines.append('  "properties": [')
    prop_lines = [f"    {json.dumps(prop)}" for prop in portfolio["properties"]]
    lines.append(",\n".join(prop_lines))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    portfolio = json.loads(PORTFOLIO.read_text(encoding="utf-8"))
    shapes = assign_roof_shapes(portfolio["properties"])
    for prop in portfolio["properties"]:
        prop["roof_shape"] = shapes[prop["property_id"]]
    if "roof_shape" not in portfolio["missing_input"]:
        portfolio["missing_input"] += MISSING_INPUT_ROOF_SHAPE_NOTE
    PORTFOLIO.write_text(render_portfolio(portfolio), encoding="utf-8")

    for prop in portfolio["properties"]:
        print(f"{prop['property_id']:6s} {prop['roof_shape']}")
    print(f"wrote {PORTFOLIO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
