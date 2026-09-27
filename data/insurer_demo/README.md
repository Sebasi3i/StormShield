# Sample insurer workbook extract

`workbook_extract.json` is the hand-checked extract of
`StormShield_Premium_Discount_POC_Revised.xlsx` that the sample-insurer specification
(27 September 2026) shipped as its companion seed. The workbook itself is not in this
repository; its SHA-256 is recorded in the extract under `source_hashes_sha256`, and
`backend/scripts/import_insurer_workbook.py --workbook <path>` verifies a local copy
against it before trusting the extract.

What the extract carries, and where each value came from in the workbook:

| Extract field | Workbook cells |
|---|---|
| `credit_plan` (none / shutters / roof_straps / both) | `Discount Tier Assumptions!A5:B8` |
| `zone_rates` (HVHZ / other Florida) | `Discount Tier Assumptions!A12:B13` |
| one policy per row: home value, zone, current and proposed features, quote | `Synthetic Policies (yours)!A5:AA14`, one row per policy, recorded per policy under `source.range` |
| `reference_totals.workbook_reference` | `Portfolio Summary!B4:B16` |

Everything in it is illustrative demo input. The rates, credits, zone assignments,
quotes and policy terms are the workbook author's assumptions; the ten properties are
the app's own demo portfolio (`backend/app/fixtures/example_portfolio.json`), whose
class and roof shape are themselves assigned placeholders.

The extract also carries the specification's *normalized* view of each policy
(`installed_features`, `credited_features`, `proposal.features_added`,
`reference_outputs`). The importer does not copy those: it re-derives them from the
workbook's original values and the normalization rule, then checks that it lands on the
same numbers. The three runtime fixtures it writes are

- `backend/app/fixtures/premium_credit_plan.json`
- `backend/app/fixtures/insurer_policies.json`
- `backend/app/fixtures/insurer_demo.json`

Regenerate with, from `backend/`:

    python scripts/import_insurer_workbook.py
    python scripts/import_insurer_workbook.py --workbook /path/to/StormShield_Premium_Discount_POC_Revised.xlsx
