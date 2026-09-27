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
  | {
      kind: 'simulated_climate'
      storms_per_year?: number | null
      climatology_id?: string | null
      // Echoed back by the server.
      sample_storms?: number
      evidence_status?: string
    }

export interface ClimatologySummary {
  climatology_id: string
  evidence_status: string
  sample_storms: number
  summary: string
  storms_per_year: {
    default: string
    note: string
    recent: { from_year: number; to_year: number; storms: number; years: number; storms_per_year: number }
    whole_record: { from_year: number; to_year: number; storms: number; years: number; storms_per_year: number }
  }
  simulator: { version: string; seed: number; num_storms: number }
  pruning: { storms_through_wind_field: number; storms_pruned: number }
  generated_at: string
}

export interface ClimatePolicyRow {
  property_id: string
  current_features: string[]
  resulting_features: string[]
  share_of_storms_with_damage: number
  share_of_storms_with_payout: number
  expected_annual_repair_cost_usd: number
  expected_annual_repair_cost_after_usd: number
  expected_annual_payout_usd: number
  expected_annual_payout_after_usd: number
  expected_annual_avoided_payout_usd: number
  expected_annual_avoided_repair_cost_usd: number
  expected_annual_avoided_uninsured_damage_usd: number
}

export interface ClimateResult {
  climatology_id: string
  sample_storms: number
  storms_per_year: number
  share_of_storms: { with_any_repair_cost: number; with_any_payout_current: number; with_any_payout_after: number }
  probability_of_a_year_with_any_payout: { current: number; after: number }
  expected_annual: {
    current_repair_cost_usd: number
    after_repair_cost_usd: number
    current_payout_usd: number
    after_payout_usd: number
    current_uninsured_damage_usd: number
    after_uninsured_damage_usd: number
    avoided_repair_cost_usd: number
    avoided_payout_usd: number
    avoided_uninsured_damage_usd: number
  }
  return_periods_current_payout: Record<string, number | null>
  largest_simulated_storms: { storm_id: string; peak_wind_kt: number; max_gust_at_a_property_mph: number; current_payout_usd: number; after_payout_usd: number }[]
  per_policy: Record<string, ClimatePolicyRow>
  notes: string[]
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
  climatology: ClimatologySummary | null
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
  break_even_storms_per_year: number | null
  homeowner: {
    expected_annual_avoided_uninsured_damage_usd: number
    premium_only_npv_usd: number
    expanded_npv_including_avoided_uninsured_damage_usd: number
  }
  assumption:
    | { kind: 'one_event_or_none'; annual_event_probability: number; no_event_probability: number }
    | { kind: 'simulated_climate'; climatology_id: string; sample_storms: number; storms_per_year: number }
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
  climate: ClimateResult | null
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
