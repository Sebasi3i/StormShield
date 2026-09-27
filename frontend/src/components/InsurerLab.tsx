import { useEffect, useMemo, useRef, useState } from 'react'

import { compareInsurer, getInsurerDemo, optimizeInsurer } from '../api/insurer'
import type {
  AnnualModel,
  InsurerCompareRequest,
  InsurerCompareResponse,
  InsurerDemo,
  InsurerProgram,
} from '../types/Insurer'

/*
 * The Insurer Lab: a fictional insurer on the ten demo properties, its current book
 * against the same book after the proposed mitigation projects. Every number here is
 * computed on the server; this screen collects settings, sends ids, and formats what
 * comes back. Nothing is a company profit figure, and nothing is annual unless the
 * illustrative annual assumptions are switched on explicitly.
 */

const STORAGE_KEY = 'stormshield.insurer-lab.config.v1'

const ARM_LABELS: Record<string, string> = {
  current_book: 'Current book',
  homeowner_funded: 'Homeowner-funded',
  insurer_cofunded: 'Insurer co-funded',
}

type AnnualMode = 'off' | 'invented'

interface StoredConfig {
  program: InsurerProgram
  stormIds: string[]
  policyIds: string[] | null
  selectedProposalIds: string[] | null
  annualMode: AnnualMode
  probability: number
  weights: Record<string, number>
  deductibleFraction: number | null
}

function usd(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value)
}

function usdCents(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

function pct(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined) return '—'

  return `${(value * 100).toFixed(digits)}%`
}

function featureList(features: string[]) {
  return features.length === 0
    ? 'none'
    : features
        .map((feature) => feature.replace('_', ' '))
        .join(' + ')
}

function readStored(): StoredConfig | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)

    return raw ? (JSON.parse(raw) as StoredConfig) : null
  } catch {
    return null
  }
}

function writeStored(config: StoredConfig) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
  } catch {
    // Storage unavailable: the demo still works, it just forgets its settings.
  }
}

function download(name: string, content: string, type: string) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')

  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}

function toCsv(result: InsurerCompareResponse) {
  const header = [
    'storm_id', 'policy_id', 'peak_gust_mph', 'current_damage_usd', 'result_damage_usd',
    'current_payout_usd', 'result_payout_usd', 'current_uninsured_damage_usd',
    'result_uninsured_damage_usd', 'avoided_payout_usd', 'avoided_uninsured_damage_usd',
    'current_curve_id', 'result_curve_id',
  ]
  const lines = result.loss_rows.map((row) =>
    header.map((key) => String(row[key as keyof typeof row])).join(','),
  )
  const assumptions = [
    `# schema_version,${result.schema_version}`,
    `# preset_id,${result.preset_id}`,
    `# selected_proposal_ids,${result.selected_proposal_ids.join(' ')}`,
    `# program,${JSON.stringify(result.program).replace(/,/g, ';')}`,
    `# provenance,${JSON.stringify(result.provenance).replace(/,/g, ';')}`,
  ]

  return [...assumptions, header.join(','), ...lines].join('\n')
}

/*
 * The settings a result was computed with, in one line, read from the response rather
 * than from the form so it always describes what is on screen.
 */
function describeSettings(result: InsurerCompareResponse, demo: InsurerDemo, proposalCount: number): string[] {
  const parts: string[] = []
  const preset = demo.presets[result.preset_id]

  parts.push(`Preset: ${preset ? preset.label : result.preset_id}`)
  parts.push(
    result.policy_ids.length === demo.policies.length
      ? `Policies: all ${demo.policies.length}`
      : `Policies: ${result.policy_ids.length} of ${demo.policies.length} (${result.policy_ids.join(', ')})`,
  )
  parts.push(`Storms: ${result.storm_ids.join(', ')}`)
  parts.push(
    result.selected_proposal_ids.length === proposalCount
      ? `Projects: all ${proposalCount}`
      : `Projects: ${result.selected_proposal_ids.length} of ${proposalCount}` +
          (result.selected_proposal_ids.length > 0 ? ` (${result.selected_proposal_ids.join(', ')})` : ''),
  )
  parts.push(
    result.deductible_fraction === null
      ? 'Deductible: policy plan, 5% of Coverage A'
      : `Deductible: ${pct(result.deductible_fraction)} of Coverage A (sensitivity, premium held fixed)`,
  )
  if (result.annual_model.kind === 'one_event_or_none') {
    const weights = Object.entries(result.annual_model.conditional_storm_weights)
    const equal = weights.every(([, w]) => w === weights[0][1])

    parts.push(
      `Annual assumptions: invented, ${pct(result.annual_model.annual_event_probability)} chance per year of one storm` +
        (equal ? ', equal weights' : `, weights ${weights.map(([s, w]) => `${s} ${w}`).join(' / ')}`),
    )
  } else {
    parts.push('Annual assumptions: off (each storm on its own, no yearly figures)')
  }
  const program = result.program
  parts.push(
    `Program: grant ${pct(program.grant_share)} of cost up to ${usd(program.grant_cap_usd)}, ` +
      `${usd(program.inspection_usd_per_project)} inspection per project, ${usd(program.fixed_setup_usd)} setup, ` +
      `${usd(program.annual_admin_usd)}/yr admin, ${usd(program.budget_usd)} budget, ` +
      `${program.horizon_years} years at ${pct(program.discount_rate, 1)}`,
  )

  return parts
}

function sameSettings(
  result: InsurerCompareResponse,
  request: InsurerCompareRequest,
  allProposalIds: string[],
  allPolicyIds: string[],
): boolean {
  const sorted = (ids: string[]) => [...ids].sort().join(',')
  const requested = request.selected_proposal_ids ?? allProposalIds

  if (sorted(result.policy_ids) !== sorted(request.policy_ids ?? allPolicyIds)) return false
  if (sorted(result.storm_ids) !== sorted(request.storm_ids ?? [])) return false
  if (sorted(result.selected_proposal_ids) !== sorted(requested)) return false
  if ((result.deductible_fraction ?? null) !== (request.deductible_fraction ?? null)) return false
  if (JSON.stringify(result.program) !== JSON.stringify(request.program)) return false
  if (result.annual_model.kind !== request.annual_model.kind) return false
  if (result.annual_model.kind === 'one_event_or_none' && request.annual_model.kind === 'one_event_or_none') {
    if (result.annual_model.annual_event_probability !== request.annual_model.annual_event_probability) return false
    if (JSON.stringify(result.annual_model.conditional_storm_weights) !== JSON.stringify(request.annual_model.conditional_storm_weights)) return false
  }

  return true
}

interface InsurerLabProps {
  // The map's current selection (frontend property ids). When there is one, the Lab
  // opens on the policies for those properties; otherwise on the whole book.
  mapSelectedPropertyIds: number[]
  onClose: () => void
}

function InsurerLab({ mapSelectedPropertyIds, onClose }: InsurerLabProps) {
  const [demo, setDemo] = useState<InsurerDemo | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [program, setProgram] = useState<InsurerProgram | null>(null)
  const [stormIds, setStormIds] = useState<string[]>([])
  const [policyIds, setPolicyIds] = useState<string[] | null>(null)
  const autoRan = useRef(false)
  const [selectedProposalIds, setSelectedProposalIds] = useState<string[] | null>(null)
  const [annualMode, setAnnualMode] = useState<AnnualMode>('off')
  const [probability, setProbability] = useState(0.1)
  const [weights, setWeights] = useState<Record<string, number>>({})
  const [deductibleFraction, setDeductibleFraction] = useState<number | null>(null)
  const [assumptionsOpen, setAssumptionsOpen] = useState(false)
  const [focusedStormId, setFocusedStormId] = useState<string | null>(null)

  const [result, setResult] = useState<InsurerCompareResponse | null>(null)
  const [running, setRunning] = useState<'compare' | 'optimize' | null>(null)
  const [runError, setRunError] = useState<string | null>(null)

  // Load the demo bundle once, then the stored settings (or the seed's defaults).
  useEffect(() => {
    let cancelled = false

    getInsurerDemo()
      .then((loaded) => {
        if (cancelled) return

        setDemo(loaded)
        const stored = readStored()
        const validStorms = (ids: string[]) =>
          ids.filter((id) => loaded.available_storm_ids.includes(id))

        setProgram(stored?.program ?? loaded.program_defaults)
        setStormIds(
          stored && validStorms(stored.stormIds).length > 0
            ? validStorms(stored.stormIds)
            : loaded.available_storm_ids,
        )
        // The map's selection wins over whatever was stored; with no selection, the
        // stored book, else everything.
        const fromMap = loaded.policies
          .filter((p) => mapSelectedPropertyIds.includes(p.frontend_property_id))
          .map((p) => p.policy_id)
        const known = new Set(loaded.policies.map((p) => p.policy_id))
        const storedIds = stored?.policyIds?.filter((id) => known.has(id)) ?? null
        setPolicyIds(
          fromMap.length > 0
            ? fromMap.length === loaded.policies.length ? null : fromMap
            : storedIds && storedIds.length > 0 ? storedIds : null,
        )
        setSelectedProposalIds(stored?.selectedProposalIds ?? null)
        setAnnualMode(stored?.annualMode === 'invented' ? 'invented' : 'off')
        setProbability(
          stored?.probability ?? loaded.optional_annual_preset.annual_event_probability,
        )
        setWeights(stored?.weights ?? loaded.optional_annual_preset.conditional_storm_weights)
        setDeductibleFraction(stored?.deductibleFraction ?? null)
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : 'Failed to load the sample insurer')
        }
      })

    return () => {
      cancelled = true
    }
    // The map selection is read once, when the Lab opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Remember the settings for next time.
  useEffect(() => {
    if (!program) return

    writeStored({
      program,
      stormIds,
      policyIds,
      selectedProposalIds,
      annualMode,
      probability,
      weights,
      deductibleFraction,
    })
  }, [program, stormIds, policyIds, selectedProposalIds, annualMode, probability, weights, deductibleFraction])

  const annualModel: AnnualModel = useMemo(() => {
    if (annualMode === 'off') return { kind: 'event_only' }

    const conditional: Record<string, number> = {}
    stormIds.forEach((id) => {
      conditional[id] = weights[id] ?? 1
    })

    return {
      kind: 'one_event_or_none',
      annual_event_probability: probability,
      conditional_storm_weights: conditional,
    }
  }, [annualMode, probability, weights, stormIds])

  const proposalIdsFor = (records: InsurerDemo['policies']) =>
    records.map((p) => p.proposal?.proposal_id).filter((id): id is string => Boolean(id))

  const request = (): InsurerCompareRequest | null => {
    if (!demo || !program || stormIds.length === 0 || (policyIds !== null && policyIds.length === 0)) return null

    const inRun = demo.policies.filter((p) => policyIds === null || policyIds.includes(p.policy_id))
    const inScope = new Set(proposalIdsFor(inRun))

    return {
      policy_ids: policyIds ?? undefined,
      storm_ids: stormIds,
      selected_proposal_ids: selectedProposalIds ? selectedProposalIds.filter((id) => inScope.has(id)) : undefined,
      deductible_fraction: deductibleFraction,
      program,
      annual_model: annualModel,
    }
  }

  const run = async (mode: 'compare' | 'optimize') => {
    const body = request()

    if (!body) return

    try {
      setRunning(mode)
      setRunError(null)
      const response =
        mode === 'optimize' ? await optimizeInsurer(body) : await compareInsurer(body)

      setResult(response)
      if (mode === 'optimize' && response.optimization) {
        setSelectedProposalIds(response.optimization.selected_proposal_ids)
      }
      if (!focusedStormId || !response.storm_ids.includes(focusedStormId)) {
        setFocusedStormId(response.storm_ids[0] ?? null)
      }
    } catch (error) {
      setRunError(error instanceof Error ? error.message : 'Request failed')
    } finally {
      setRunning(null)
    }
  }

  // Run once as soon as the settings are in place, so the Lab opens on numbers.
  useEffect(() => {
    if (!demo || !program || autoRan.current) return

    autoRan.current = true
    void run('compare')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demo, program])

  const resetToSeed = () => {
    if (!demo) return

    setProgram(demo.program_defaults)
    setStormIds(demo.available_storm_ids)
    setPolicyIds(null)
    setSelectedProposalIds(null)
    setAnnualMode('off')
    setProbability(demo.optional_annual_preset.annual_event_probability)
    setWeights(demo.optional_annual_preset.conditional_storm_weights)
    setDeductibleFraction(null)
    setResult(null)
  }

  const toggleStorm = (id: string) => {
    setStormIds((current) =>
      current.includes(id) ? current.filter((s) => s !== id) : [...current, id],
    )
  }

  const togglePolicy = (policyId: string) => {
    if (!demo) return

    const all = demo.policies.map((p) => p.policy_id)
    const current = policyIds ?? all
    const next = current.includes(policyId) ? current.filter((id) => id !== policyId) : all.filter((id) => id === policyId || current.includes(id))

    setPolicyIds(next.length === all.length ? null : next)
  }

  const toggleProposal = (proposalId: string) => {
    if (!demo) return

    const all = proposalIdsFor(demo.policies.filter((p) => policyIds === null || policyIds.includes(p.policy_id)))
    const current = selectedProposalIds ?? all
    const next = current.includes(proposalId)
      ? current.filter((id) => id !== proposalId)
      : [...current, proposalId]

    setSelectedProposalIds(next.length === all.length ? null : next)
  }

  const updateProgram = (field: keyof InsurerProgram, value: number) => {
    setProgram((current) => (current ? { ...current, [field]: value } : current))
  }

  if (loadError) {
    return (
      <div className="lab">
        <div className="lab-error" role="alert">{loadError}</div>
      </div>
    )
  }

  if (!demo || !program) {
    return <div className="lab"><p className="lab-loading">Loading the sample insurer…</p></div>
  }

  const preset = demo.presets[demo.default_preset_id]
  const policiesInRun = demo.policies.filter((p) => policyIds === null || policyIds.includes(p.policy_id))
  const allPolicyIds = demo.policies.map((p) => p.policy_id)
  const insuredValue = policiesInRun.reduce((total, p) => total + p.coverage_a_usd, 0)
  const currentPremium = result ? result.programs.current_book.premium.current_wind_premium_usd : null
  const pricedById = new Map((result?.policies ?? []).map((p) => [p.policy_id, p]))
  const allProposalIds = proposalIdsFor(policiesInRun)
  const selectedSet = new Set(selectedProposalIds ?? allProposalIds)
  const mapPolicyIds = demo.policies
    .filter((p) => mapSelectedPropertyIds.includes(p.frontend_property_id))
    .map((p) => p.policy_id)
  const focusedEvent = result && focusedStormId ? result.events[focusedStormId] : null
  const cofunded = result?.programs.insurer_cofunded ?? null
  const annualOn = annualMode !== 'off'
  const currentRequest = request()
  const settingsLines = result ? describeSettings(result, demo, allProposalIds.length) : []
  const resultIsStale = result !== null && currentRequest !== null && !sameSettings(result, currentRequest, allProposalIds, allPolicyIds)

  return (
    <div className="lab">
      <div className="lab-header">
        <div>
          <span className="analysis-eyebrow">INSURER LAB · FICTIONAL</span>
          <h2>{demo.insurer.name}</h2>
          <p>
            {policiesInRun.length === demo.policies.length
              ? `${demo.policies.length} policies`
              : `${policiesInRun.length} of ${demo.policies.length} policies`}
            {mapPolicyIds.length > 0 && policiesInRun.length === mapPolicyIds.length ? ' (the map\'s selection)' : ''} ·{' '}
            {usd(insuredValue)} insured value · {currentPremium !== null ? `${usd(currentPremium)} current annual wind premium` : 'premium: run Compare'} ·
            preset “{preset.label}”
          </p>
        </div>

        <div className="lab-header-actions">
          <button type="button" className="lab-secondary" onClick={() => setAssumptionsOpen((o) => !o)}>
            {assumptionsOpen ? 'Hide assumptions' : 'Assumptions'}
          </button>
          <button type="button" className="lab-secondary" onClick={onClose}>
            Back to map
          </button>
        </div>
      </div>

      <div className="analysis-notice">
        <strong>Illustrative demo — every rate, credit, quote and policy term is an assumption</strong>
        <span>
          Gross payouts before reinsurance, claims expense, tax and commission. The
          selected projects are assumed completed; a grant does not by itself change
          what gets built.
        </span>
      </div>

      {assumptionsOpen && (
        <section className="lab-drawer">
          <div className="lab-drawer-grid">
            <div>
              <h4>Preset</h4>
              <p>{preset.purpose}</p>
              {demo.policies
                .filter((p) => p.states[demo.default_preset_id]?.normalization_note)
                .map((p) => (
                  <p key={p.policy_id} className="lab-note">
                    {p.policy_id}: {p.states[demo.default_preset_id].normalization_note}
                  </p>
                ))}
            </div>

            <div>
              <h4>Credits and rates</h4>
              <table className="lab-mini-table">
                <tbody>
                  {Object.entries(demo.credit_plan.credit_plan).map(([key, value]) => (
                    <tr key={key}><td>{key.replace(/_/g, ' ')}</td><td>{pct(value)}</td></tr>
                  ))}
                  {Object.entries(demo.credit_plan.zone_rates).map(([key, value]) => (
                    <tr key={key}><td>rate {key.replace(/_/g, ' ')}</td><td>{pct(value, 1)}</td></tr>
                  ))}
                </tbody>
              </table>
              <p className="lab-note">{demo.credit_plan.credit_basis}</p>
            </div>

            <div>
              <h4>Deductible</h4>
              <label className="lab-field">
                <span>Fraction of Coverage A</span>
                <select
                  value={deductibleFraction ?? ''}
                  onChange={(e) => setDeductibleFraction(e.target.value === '' ? null : Number(e.target.value))}
                >
                  <option value="">Policy plan (5%)</option>
                  {demo.deductible_sensitivity_fractions.map((f) => (
                    <option key={f} value={f}>{pct(f)} (sensitivity, premium held fixed)</option>
                  ))}
                </select>
              </label>
            </div>

            <div>
              <h4>Program</h4>
              {(
                [
                  ['grant_share', 'Grant share of cost', 0.01],
                  ['grant_cap_usd', 'Grant cap ($)', 100],
                  ['inspection_usd_per_project', 'Inspection per project ($)', 10],
                  ['fixed_setup_usd', 'Fixed setup ($)', 50],
                  ['annual_admin_usd', 'Annual administration ($)', 50],
                  ['budget_usd', 'Upfront budget ($)', 500],
                  ['horizon_years', 'Horizon (years)', 1],
                  ['discount_rate', 'Discount rate', 0.005],
                ] as [keyof InsurerProgram, string, number][]
              ).map(([field, label, step]) => (
                <label key={field} className="lab-field">
                  <span>{label}</span>
                  <input
                    type="number"
                    step={step}
                    min={0}
                    value={program[field]}
                    onChange={(e) => updateProgram(field, Number(e.target.value))}
                  />
                </label>
              ))}
              <p className="lab-note">{demo.program_note}</p>
              <button type="button" className="lab-secondary" onClick={resetToSeed}>
                Reset to seed
              </button>
            </div>
          </div>
        </section>
      )}

      <section className="lab-controls">
        <div>
          <h4>Policies in this run</h4>
          <div className="lab-checks lab-checks-column">
            {demo.policies.map((p) => (
              <label key={p.policy_id}>
                <input
                  type="checkbox"
                  checked={policyIds === null || policyIds.includes(p.policy_id)}
                  onChange={() => togglePolicy(p.policy_id)}
                />
                {p.policy_id} <span className="lab-sub-inline">{p.property.address}</span>
              </label>
            ))}
          </div>
          <div className="lab-header-actions">
            <button type="button" className="lab-secondary lab-small" onClick={() => setPolicyIds(null)}>
              All
            </button>
            {mapPolicyIds.length > 0 && (
              <button
                type="button"
                className="lab-secondary lab-small"
                onClick={() => setPolicyIds(mapPolicyIds.length === allPolicyIds.length ? null : mapPolicyIds)}
              >
                Map selection ({mapPolicyIds.length})
              </button>
            )}
          </div>
        </div>

        <div>
          <h4>Storms</h4>
          <div className="lab-checks">
            {demo.available_storm_ids.map((id) => (
              <label key={id}>
                <input type="checkbox" checked={stormIds.includes(id)} onChange={() => toggleStorm(id)} />
                {id}
              </label>
            ))}
          </div>
        </div>

        <div>
          <h4>Yearly figures</h4>
          <div className="lab-radios">
            <label className="lab-toggle">
              <input type="radio" name="annual-mode" checked={annualMode === 'off'} onChange={() => setAnnualMode('off')} />
              Off: each storm on its own
            </label>
            <label className="lab-toggle">
              <input type="radio" name="annual-mode" checked={annualMode === 'invented'} onChange={() => setAnnualMode('invented')} />
              Invented probability of one catalog storm per year
            </label>
          </div>
          {annualMode === 'invented' && (
            <div className="lab-annual">
              <label className="lab-field">
                <span>Chance of one storm per year (%)</span>
                <input
                  type="number"
                  step={1}
                  min={0}
                  max={100}
                  value={Math.round(probability * 1000) / 10}
                  onChange={(e) => setProbability(Math.min(100, Math.max(0, Number(e.target.value))) / 100)}
                />
              </label>
              {stormIds.map((id) => (
                <label key={id} className="lab-field">
                  <span>Weight {id}</span>
                  <input
                    type="number"
                    step={0.1}
                    min={0}
                    value={weights[id] ?? 1}
                    onChange={(e) => setWeights((w) => ({ ...w, [id]: Number(e.target.value) }))}
                  />
                </label>
              ))}
            </div>
          )}
        </div>

        <div className="lab-actions">
          <button
            type="button"
            className="analyze-button"
            disabled={running !== null || stormIds.length === 0 || (policyIds !== null && policyIds.length === 0)}
            onClick={() => run('compare')}
          >
            {running === 'compare' ? 'Comparing…' : 'Compare'}
          </button>
          <button
            type="button"
            className="lab-secondary"
            disabled={running !== null || !annualOn || stormIds.length === 0}
            title={annualOn ? '' : 'Needs yearly figures: pick an annual assumption'}
            onClick={() => run('optimize')}
          >
            {running === 'optimize' ? 'Optimizing…' : 'Optimize within budget'}
          </button>
          {result && (
            <>
              <button
                type="button"
                className="lab-secondary"
                onClick={() => download('insurer-lab.json', JSON.stringify(result, null, 2), 'application/json')}
              >
                Export JSON
              </button>
              <button
                type="button"
                className="lab-secondary"
                onClick={() => download('insurer-lab-losses.csv', toCsv(result), 'text/csv')}
              >
                Export CSV
              </button>
            </>
          )}
        </div>
      </section>

      {runError && <div className="lab-error" role="alert">{runError}</div>}

      {result && (
        <div className={resultIsStale ? 'lab-settings stale' : 'lab-settings'} aria-live="polite">
          <strong>
            {resultIsStale
              ? 'Settings changed since this result was computed. Press Compare to refresh. The result below used:'
              : `This result was computed with${result.optimization ? ' (after Optimize)' : ''}:`}
          </strong>
          <ul>
            {settingsLines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      {result?.optimization && (
        <div className="analysis-callout">
          <strong>{result.optimization.verdict}</strong>
          <p>
            Selected {result.optimization.selected_proposal_ids.join(', ') || 'none'} for{' '}
            {usd(result.optimization.insurer_upfront_usd)} upfront ({usd(result.optimization.budget_remaining_usd)} of
            budget left); insurer NPV {usd(result.optimization.insurer_npv_usd)} over{' '}
            {result.optimization.subsets_evaluated} subsets.
            {result.optimization.rejected_proposal_ids.length > 0 &&
              ` Rejected: ${result.optimization.rejected_proposal_ids.join(', ')}.`}
            {result.optimization.excluded.length > 0 &&
              ` Excluded (cost unknown): ${result.optimization.excluded.map((e) => e.proposal_id).join(', ')}.`}
          </p>
        </div>
      )}

      {/* ---------------- Policies ---------------- */}
      <section className="analysis-section">
        <div className="analysis-section-heading">
          <div>
            <span>POLICIES</span>
            <h3>Book and proposals</h3>
          </div>
          <p>Tick a proposal to include it in the program</p>
        </div>

        <div className="analysis-table-wrapper">
          <table className="analysis-table lab-table">
            <thead>
              <tr>
                <th>Policy</th>
                <th>Class / roof</th>
                <th>Installed</th>
                <th>Requested → new</th>
                <th>Quote</th>
                <th>Wind premium now → after</th>
                <th>Owner saves / yr</th>
                <th>Grant</th>
                <th>Owner pays</th>
                <th>Readiness</th>
              </tr>
            </thead>
            <tbody>
              {policiesInRun.map((policy) => {
                const state = policy.states[demo.default_preset_id]
                const priced = pricedById.get(policy.policy_id)
                const proposalId = policy.proposal?.proposal_id ?? null

                return (
                  <tr key={policy.policy_id}>
                    <td>
                      <label className="lab-row-check">
                        {proposalId && (
                          <input
                            type="checkbox"
                            checked={selectedSet.has(proposalId)}
                            onChange={() => toggleProposal(proposalId)}
                          />
                        )}
                        <span>
                          <strong>{policy.policy_id}</strong>
                          <span>{policy.property.address}, {policy.property.city}</span>
                        </span>
                      </label>
                    </td>
                    <td>{policy.property.vulnerability_class.replace('_', ' ')} / {policy.property.roof_shape}</td>
                    <td>{featureList(state.installed_features)}</td>
                    <td>
                      {policy.proposal ? featureList(policy.proposal.requested_features) : '—'}
                      {priced && (
                        <span className="lab-sub"> → {priced.features_added.length ? featureList(priced.features_added) : 'no new project'}</span>
                      )}
                    </td>
                    <td>{usd(policy.proposal?.quote_usd ?? null)}</td>
                    <td>
                      {priced
                        ? `${usdCents(priced.current_wind_premium_usd)} → ${usdCents(priced.result_wind_premium_usd)}`
                        : '—'}
                      <span className="lab-sub">total premium: not supplied</span>
                    </td>
                    <td>{priced ? usdCents(priced.annual_premium_foregone_usd) : '—'}</td>
                    <td>{priced ? usdCents(priced.insurer_grant_usd) : '—'}</td>
                    <td>{priced ? usdCents(priced.homeowner_upfront_usd) : '—'}</td>
                    <td>
                      {!priced
                        ? '—'
                        : priced.cost_unavailable_reason
                          ? priced.cost_unavailable_reason.replace(/_/g, ' ')
                          : !priced.selected
                            ? 'not selected'
                            : priced.is_project
                              ? priced.homeowner.premium_only_payback_years !== null
                                ? `payback ${priced.homeowner.premium_only_payback_years.toFixed(1)} yr`
                                : priced.homeowner.payback_note
                              : 'no new project'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      {result && (
        <>
          {/* ---------------- Events ---------------- */}
          <section className="analysis-section">
            <div className="analysis-section-heading">
              <div>
                <span>EVENT COMPARISON</span>
                <h3>Each storm as an alternative event</h3>
              </div>
              <p>Never added across storms</p>
            </div>

            <div className="analysis-table-wrapper">
              <table className="analysis-table lab-table">
                <thead>
                  <tr>
                    <th>Storm</th>
                    <th>Damage now → after</th>
                    <th>Insurer payout now → after</th>
                    <th>Avoided payout</th>
                    <th>Uninsured now → after</th>
                    <th>If this storm hits in year 1, co-funded</th>
                  </tr>
                </thead>
                <tbody>
                  {result.storm_ids.map((id) => {
                    const event = result.events[id]

                    return (
                      <tr
                        key={id}
                        className={id === focusedStormId ? 'lab-focused' : undefined}
                        onClick={() => setFocusedStormId(id)}
                      >
                        <td><strong>{id}</strong></td>
                        <td>{usd(event.current_book.damage_usd)} → {usd(event.program.damage_usd)}</td>
                        <td>{usd(event.current_book.payout_usd)} → {usd(event.program.payout_usd)}</td>
                        <td className="analysis-savings">{usd(event.avoided_payout_usd)}</td>
                        <td>{usd(event.current_book.uninsured_damage_usd)} → {usd(event.program.uninsured_damage_usd)}</td>
                        <td className={
                          (event.first_year_insurer_benefit_if_this_storm_usd.insurer_cofunded ?? 0) < 0
                            ? 'lab-negative'
                            : 'analysis-savings'
                        }>
                          {usd(event.first_year_insurer_benefit_if_this_storm_usd.insurer_cofunded)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {focusedEvent && (
              <>
                <p className="lab-note">
                  Policy detail for {focusedEvent.storm_id}. With no event, payout savings are zero
                  while the premium discount and program costs still occur.
                </p>
                <div className="analysis-table-wrapper">
                  <table className="analysis-table lab-table">
                    <thead>
                      <tr>
                        <th>Policy</th>
                        <th>Gust</th>
                        <th>Curve now → after</th>
                        <th>Damage now → after</th>
                        <th>Payout now → after</th>
                        <th>Uninsured now → after</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.loss_rows
                        .filter((row) => row.storm_id === focusedEvent.storm_id)
                        .map((row) => (
                          <tr key={row.policy_id}>
                            <td><strong>{row.policy_id}</strong>{row.no_op && <span className="lab-sub">no change</span>}</td>
                            <td>{row.peak_gust_mph.toFixed(1)} mph</td>
                            <td className="lab-curve">{row.current_curve_id} → {row.result_curve_id}</td>
                            <td>{usd(row.current_damage_usd)} → {usd(row.result_damage_usd)}</td>
                            <td>{usd(row.current_payout_usd)} → {usd(row.result_payout_usd)}</td>
                            <td>{usd(row.current_uninsured_damage_usd)} → {usd(row.result_uninsured_damage_usd)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>

          {/* ---------------- Programs ---------------- */}
          <section className="analysis-section">
            <div className="analysis-section-heading">
              <div>
                <span>PROGRAM COMPARISON</span>
                <h3>Who pays what</h3>
              </div>
              <p>Same projects, same physics; only the cash differs</p>
            </div>

            <div className="analysis-table-wrapper">
              <table className="analysis-table lab-table">
                <thead>
                  <tr>
                    <th>Arm</th>
                    <th>Projects</th>
                    <th>Premium foregone / yr</th>
                    <th>Insurer grants</th>
                    <th>Insurer upfront</th>
                    <th>Homeowner upfront</th>
                    {annualOn && <th>Expected avoided payout / yr</th>}
                    {annualOn && <th>Insurer NPV</th>}
                    {annualOn && <th>Break-even avoided / yr</th>}
                    {annualOn && <th>Break-even event probability</th>}
                  </tr>
                </thead>
                <tbody>
                  {Object.values(result.programs).map((arm) => {
                    const econ = arm.annual_economics

                    return (
                      <tr key={arm.program_id}>
                        <td><strong>{ARM_LABELS[arm.program_id] ?? arm.program_id}</strong></td>
                        <td>{arm.project_count}</td>
                        <td>{usdCents(arm.premium.annual_premium_foregone_usd)}</td>
                        <td>{usd(arm.costs.insurer_grants_usd)}</td>
                        <td>{usd(arm.costs.insurer_upfront_usd)}</td>
                        <td>{usd(arm.costs.homeowner_upfront_usd)}</td>
                        {annualOn && <td>{usd(econ?.expected_annual_avoided_payout_usd)}</td>}
                        {annualOn && (
                          <td className={(econ?.insurer_npv_usd ?? 0) < 0 ? 'lab-negative' : 'analysis-savings'}>
                            {econ ? usd(econ.insurer_npv_usd) : arm.annual_economics_unavailable_reason}
                          </td>
                        )}
                        {annualOn && <td>{usd(econ?.break_even_annual_avoided_payout_usd)}</td>}
                        {annualOn && (
                          <td>{econ?.break_even_annual_event_probability != null ? pct(econ.break_even_annual_event_probability, 1) : '—'}</td>
                        )}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {annualOn && cofunded?.annual_economics ? (
              <div className="analysis-callout">
                <strong>
                  Active assumption: {pct(cofunded.annual_economics.assumption.annual_event_probability)} chance of one
                  catalog event per year, {pct(cofunded.annual_economics.assumption.no_event_probability)} of none
                </strong>
                <p>
                  Invented for the demo. Over {result.program.horizon_years} years at{' '}
                  {pct(result.program.discount_rate, 1)}, the co-funded program breaks even if a storm like these
                  hits with probability{' '}
                  {cofunded.annual_economics.break_even_annual_event_probability != null
                    ? pct(cofunded.annual_economics.break_even_annual_event_probability, 1)
                    : '—'}{' '}
                  a year. Homeowners' expected avoided uninsured damage:{' '}
                  {usd(cofunded.annual_economics.homeowner.expected_annual_avoided_uninsured_damage_usd)} a year; their
                  premium-only NPV {usd(cofunded.annual_economics.homeowner.premium_only_npv_usd)}, or{' '}
                  {usd(cofunded.annual_economics.homeowner.expanded_npv_including_avoided_uninsured_damage_usd)} counting that damage.
                </p>
              </div>
            ) : (
              <p className="lab-note">
                No yearly figures in event-only mode: the catalog is not a frequency sample. Switch on the invented
                probability above to see them, and treat them as an illustration.
              </p>
            )}


            {result.deductible_sensitivity_note && <p className="lab-note">{result.deductible_sensitivity_note}</p>}
            {!result.complete && (
              <p className="lab-note">
                Incomplete: {result.unavailable.map((u) => `${u.proposal_id} (${u.reason.replace(/_/g, ' ')})`).join(', ')}.
                Cost-dependent totals are unavailable; premiums and physical results stand.
              </p>
            )}
          </section>

          <section className="analysis-section">
            <div className="analysis-section-heading">
              <div>
                <span>NOTES</span>
                <h3>Scope and warnings</h3>
              </div>
            </div>
            <ul className="analysis-detail-list">
              {result.notes.map((note, index) => <li key={index}>{note}</li>)}
            </ul>
            {result.warnings.length > 0 && (
              <ul className="analysis-detail-list analysis-warning-list">
                {result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  )
}

export default InsurerLab
