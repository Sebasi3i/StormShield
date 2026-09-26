export interface EligibleOption {
  property_id: string
  upgrade_ids: string[]
}

export interface StormLossRow {
  storm_id: string
  property_id: string
  upgrade_id: string
  peak_gust_mph: number
  baseline_damage_usd: number
  upgraded_damage_usd: number
  baseline_payout_usd: number
  upgraded_payout_usd: number
  avoided_payout_usd: number
}

export interface StormLossResponse {
  schema_version: string
  run_id: string
  catalog_id: string
  storm_ids: string[]
  property_ids: string[]
  eligible_options: EligibleOption[]
  evidence_status: 'assumed' | 'sourced'
  rows: StormLossRow[]
  warnings: string[]
  assumptions: string[]
  metadata: Record<string, unknown>
}