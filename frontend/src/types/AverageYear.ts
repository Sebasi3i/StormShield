/*
 * The map's "average year": expected yearly figures for the demo properties over the
 * simulated storm climatology (schema insurer-demo-v1).
 */

export interface AverageYearUpgrade {
  features_added: string[] | null
  expected_annual_repair_cost_usd: number
  expected_annual_payout_usd: number
  expected_annual_avoided_repair_cost_usd: number
  expected_annual_avoided_payout_usd: number
  expected_annual_avoided_uninsured_damage_usd: number
}

export interface AverageYearProperty {
  property_id: string
  climatology_property_id: string
  installed_features: string[] | null
  share_of_storms_with_damage: number
  probability_of_damage_in_a_year: number
  probability_of_a_claim_in_a_year: number
  expected_annual_repair_cost_usd: number
  expected_annual_payout_usd: number
  expected_annual_uninsured_damage_usd: number
  return_periods_repair_cost: Record<string, number | null>
  upgrades: Record<string, AverageYearUpgrade>
}

export interface AverageYearResponse {
  schema_version: string
  climatology_id: string
  evidence_status: string
  sample_storms: number
  storms_per_year: number
  storms_per_year_basis: {
    default: string
    recent: { from_year: number; to_year: number; storms_per_year: number }
    whole_record: { from_year: number; to_year: number; storms_per_year: number }
  }
  properties: AverageYearProperty[]
  totals: { expected_annual_repair_cost_usd: number; expected_annual_payout_usd: number }
  notes: string[]
}
