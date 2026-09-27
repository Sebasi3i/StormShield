/*
 * The sample insurer's API shapes (schema insurer-demo-v1). Only what the Insurer
 * Lab reads is typed; anything else in a response is carried as unknown.
 */

export type Feature = 'roof_straps' | 'shutters'

export interface InsurerProgram {
  grant_share: number
  grant_cap_usd: number
  inspection_usd_per_project: number
  fixed_setup_usd: number
  annual_admin_usd: number
  budget_usd: number
  horizon_years: number
  discount_rate: number
}

export type AnnualModel =
  | { kind: 'event_only' }
  | {
      kind: 'one_event_or_none'
      annual_event_probability: number
      conditional_storm_weights: Record<string, number>
    }

export interface PolicyState {
  installed_features: Feature[]
  credited_features: Feature[]
  physical_state_conflict: string | null
  normalization_note: string | null
}

export interface PolicyRecord {
  policy_id: string
  property_id: string
  frontend_property_id: number
  property: {
    address: string
    city: string
    county: string
    replacement_cost_usd: number
    vulnerability_class: string
    roof_shape: string
  }
  coverage_a_usd: number
  coverage_limit_usd: number
  deductible: { kind: 'percent'; fraction: number } | { kind: 'dollar'; amount_usd: number }
  rating_value_usd: number
  rating_zone: string
  annual_nonwind_premium_usd: number | null
  states: Record<string, PolicyState>
  proposal: {
    proposal_id: string
    requested_features: Feature[]
    quoted_scope: Feature[]
    quote_usd: number | null
    incremental_scope_confirmed: boolean
  } | null
}

export interface InsurerDemo {
  schema_version: string
  insurer: { insurer_id: string; name: string; evidence_status: string; portfolio_id: string }
  default_preset_id: string
  presets: Record<string, { label: string; purpose: string }>
  credit_plan: {
    plan_id: string
    credit_plan: Record<string, number>
    zone_rates: Record<string, number>
    credit_basis: string
    rate_basis: string
  }
  program_defaults: InsurerProgram
  program_note: string
  optional_annual_preset: {
    annual_event_probability: number
    conditional_storm_weights: Record<string, number>
  }
  deductible_sensitivity_fractions: number[]
  policies: PolicyRecord[]
  policies_note: string
  available_storm_ids: string[]
  reference_totals: Record<string, Record<string, number | string>>
}

export interface InsurerCompareRequest {
  preset_id?: string
  policy_ids?: string[]
  storm_ids?: string[]
  selected_proposal_ids?: string[]
  deductible_fraction?: number | null
  program: InsurerProgram
  annual_model: AnnualModel
}

export interface PricedPolicy {
  policy_id: string
  property_id: string
  selected: boolean
  is_project: boolean
  installed_features: Feature[]
  requested_features: Feature[]
  features_added: Feature[]
  resulting_features: Feature[]
  current_credit: number
  result_credit: number
  current_wind_premium_usd: number
  result_wind_premium_usd: number
  annual_premium_foregone_usd: number
  current_total_premium_usd: number | null
  proposal_id: string | null
  quote_usd: number | null
  effective_cost_usd: number | null
  cost_unavailable_reason: string | null
  insurer_grant_usd: number | null
  homeowner_upfront_usd: number | null
  homeowner: {
    annual_premium_savings_usd: number
    premium_only_payback_years: number | null
    payback_note: string | null
  }
}

export interface LossRow {
  policy_id: string
  storm_id: string
  peak_gust_mph: number
  current_curve_id: string
  result_curve_id: string
  no_op: boolean
  current_damage_usd: number
  result_damage_usd: number
  current_payout_usd: number
  result_payout_usd: number
  current_uninsured_damage_usd: number
  result_uninsured_damage_usd: number
  avoided_damage_usd: number
  avoided_payout_usd: number
  avoided_uninsured_damage_usd: number
}

export interface EventTotals {
  damage_usd: number
  payout_usd: number
  uninsured_damage_usd: number
}

export interface EventResult {
  storm_id: string
  current_book: EventTotals
  program: EventTotals
  avoided_damage_usd: number
  avoided_payout_usd: number
  avoided_uninsured_damage_usd: number
  first_year_insurer_benefit_if_this_storm_usd: Record<string, number | null>
}

export interface AnnualEconomics {
  pv_factor: number
  expected_annual_avoided_payout_usd: number
  annual_premium_foregone_usd: number
  insurer_upfront_usd: number
  net_annual_insurer_benefit_usd: number
  insurer_npv_usd: number
  insurer_roi: number | null
  break_even_annual_avoided_payout_usd: number
  break_even_annual_event_probability: number | null
  homeowner: {
    expected_annual_avoided_uninsured_damage_usd: number
    premium_only_npv_usd: number
    expanded_npv_including_avoided_uninsured_damage_usd: number
  }
  assumption: { annual_event_probability: number; no_event_probability: number }
}

export interface ProgramResult {
  program_id: string
  label: string
  project_count: number
  complete: boolean
  premium: {
    current_wind_premium_usd: number
    result_wind_premium_usd: number
    annual_premium_foregone_usd: number
  }
  costs: {
    project_cost_usd: number | null
    insurer_grants_usd: number | null
    inspections_usd: number | null
    fixed_setup_usd: number | null
    annual_admin_usd: number | null
    insurer_upfront_usd: number | null
    homeowner_upfront_usd: number | null
  }
  annual_economics: AnnualEconomics | null
  annual_economics_unavailable_reason: string | null
}

export interface InsurerCompareResponse {
  schema_version: string
  preset_id: string
  storm_ids: string[]
  selected_proposal_ids: string[]
  program: InsurerProgram
  annual_model: AnnualModel
  deductible_fraction: number | null
  complete: boolean
  unavailable: { policy_id: string; proposal_id: string; reason: string }[]
  policies: PricedPolicy[]
  loss_rows: LossRow[]
  events: Record<string, EventResult>
  programs: Record<string, ProgramResult>
  deductible_sensitivity_note: string | null
  warnings: string[]
  notes: string[]
  provenance: Record<string, unknown>
  optimization?: {
    selected_proposal_ids: string[]
    rejected_proposal_ids: string[]
    excluded: { proposal_id: string; reason: string }[]
    insurer_npv_usd: number
    insurer_upfront_usd: number
    budget_remaining_usd: number
    subsets_evaluated: number
    verdict: string
  }
}
