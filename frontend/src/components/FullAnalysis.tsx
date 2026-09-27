import { useEffect, useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { getAverageYear } from '../api/averageYear'
import type { AverageYearResponse } from '../types/AverageYear'
import type { Property } from '../types/Property'
import type { StormLossResponse, StormLossRow } from '../types/StormLoss'
import { buildLabel, featureLabel, upgradeLabel } from '../utils/propertyLabels'

/*
 * The report behind "Analyze Portfolio Risk". Everything is computed on the server;
 * this screen adds the rows up per storm and per property and puts them in plain words:
 * "repair cost" is the modeled cost to repair the building, and "covered by insurance"
 * is the part of that cost above each home's deductible, up to its coverage limit.
 */

interface FullAnalysisProps {
  properties: Property[]
  losses: StormLossResponse
  onClose: () => void
}

type AnalysisTab = 'overview' | 'storms' | 'properties' | 'upgrades' | 'year' | 'details'

interface StormSummary {
  stormId: string
  damage: number
  payout: number
  peakGust: number
  affectedProperties: number
}

interface PropertySummary {
  property: Property
  peakGust: number
  worstStormId: string
  worstDamage: number
  worstPayout: number
}

interface UpgradeCandidate {
  upgradeId: string
  label: string
  featuresAdded: number
  baselinePayout: number
  upgradedPayout: number
  avoidedPayout: number
}

interface UpgradeSummary {
  property: Property
  installed: string[]
  best: UpgradeCandidate | null
  candidates: UpgradeCandidate[]
  // Recharts reads these two off the row.
  address: string
  baselinePayout: number
  upgradedPayout: number
  avoidedPayout: number
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value)
}

function formatCompactCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

/*
 * Every upgrade row repeats the same baseline for its storm and property, so one row
 * per storm/property is enough for anything about the home as it is.
 */
function getBaselineRows(rows: StormLossRow[]): StormLossRow[] {
  return Array.from(
    new Map(rows.map((row) => [`${row.storm_id}:${row.property_id}`, row])).values(),
  )
}

function pct(value: number) {
  return `${(value * 100).toFixed(0)}%`
}

function FullAnalysis({ properties, losses, onClose }: FullAnalysisProps) {
  const [activeTab, setActiveTab] = useState<AnalysisTab>('overview')

  // The average year comes from the simulated climatology, independent of the storm
  // run, so it is fetched once for the selected properties.
  const [averageYear, setAverageYear] = useState<AverageYearResponse | null>(null)
  const [averageYearError, setAverageYearError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    getAverageYear(properties)
      .then((loaded) => {
        if (!cancelled) setAverageYear(loaded)
      })
      .catch((error: unknown) => {
        if (!cancelled) setAverageYearError(error instanceof Error ? error.message : 'Average-year figures unavailable')
      })

    return () => {
      cancelled = true
    }
  }, [properties])

  const baselineRows = useMemo(() => getBaselineRows(losses.rows), [losses.rows])

  const portfolioValue = properties.reduce((total, property) => total + property.value, 0)

  // Per storm: repair cost and insured cost across the selected properties.
  const stormSummaries = useMemo(
    () =>
      losses.storm_ids
        .map((stormId): StormSummary => {
          const rows = baselineRows.filter((row) => row.storm_id === stormId)

          return {
            stormId,
            damage: rows.reduce((total, row) => total + row.baseline_damage_usd, 0),
            payout: rows.reduce((total, row) => total + row.baseline_payout_usd, 0),
            peakGust: rows.length > 0 ? Math.max(...rows.map((row) => row.peak_gust_mph)) : 0,
            affectedProperties: rows.filter((row) => row.baseline_damage_usd > 0).length,
          }
        })
        .sort((a, b) => b.damage - a.damage),
    [baselineRows, losses.storm_ids],
  )

  const damagingStorms = stormSummaries.filter((storm) => storm.damage > 0)
  const worstStormByDamage = damagingStorms[0] ?? null
  const worstStormByPayout = stormSummaries.reduce<StormSummary | null>(
    (largest, storm) => (!largest || storm.payout > largest.payout ? storm : largest),
    null,
  )

  const peakGust = baselineRows.length > 0 ? Math.max(...baselineRows.map((row) => row.peak_gust_mph)) : 0

  const damagedPropertyIds = new Set(
    baselineRows.filter((row) => row.baseline_damage_usd > 0).map((row) => row.property_id),
  )

  // Per property: the storm that hurt it most.
  const propertySummaries = useMemo(
    () =>
      properties
        .map((property): PropertySummary => {
          const rows = baselineRows.filter((row) => row.property_id === String(property.id))
          const worst = rows.reduce<StormLossRow | null>(
            (current, row) => (!current || row.baseline_damage_usd > current.baseline_damage_usd ? row : current),
            null,
          )

          return {
            property,
            peakGust: rows.length > 0 ? Math.max(...rows.map((row) => row.peak_gust_mph)) : 0,
            worstStormId: worst?.storm_id ?? '—',
            worstDamage: worst?.baseline_damage_usd ?? 0,
            worstPayout: worst?.baseline_payout_usd ?? 0,
          }
        })
        .sort((a, b) => b.worstDamage - a.worstDamage),
    [baselineRows, properties],
  )

  /*
   * Per property: every upgrade the home could get, with the insured cost it would
   * avoid across all the storms shown, and the best of them. Ties go to the smaller
   * change; when no upgrade changes anything there is no best.
   */
  const upgradeSummaries = useMemo(
    () =>
      properties
        .map((property): UpgradeSummary => {
          const propertyRows = losses.rows.filter((row) => row.property_id === String(property.id))
          const installed = propertyRows[0]?.installed_features ?? []
          const upgradeIds = Array.from(new Set(propertyRows.map((row) => row.upgrade_id)))

          const candidates = upgradeIds
            .map((upgradeId): UpgradeCandidate => {
              const rows = propertyRows.filter((row) => row.upgrade_id === upgradeId)
              const baselinePayout = rows.reduce((total, row) => total + row.baseline_payout_usd, 0)
              const upgradedPayout = rows.reduce((total, row) => total + row.upgraded_payout_usd, 0)

              return {
                upgradeId,
                label: upgradeLabel(rows[0]),
                featuresAdded: rows[0].features_added?.length ?? 1,
                baselinePayout,
                upgradedPayout,
                avoidedPayout: baselinePayout - upgradedPayout,
              }
            })
            .sort((a, b) => b.avoidedPayout - a.avoidedPayout || a.featuresAdded - b.featuresAdded)

          const best = candidates.length > 0 && candidates[0].avoidedPayout > 0 ? candidates[0] : null
          const baselinePayout = candidates[0]?.baselinePayout ?? 0

          return {
            property,
            installed,
            best,
            candidates,
            address: property.address,
            baselinePayout,
            upgradedPayout: best ? best.upgradedPayout : baselinePayout,
            avoidedPayout: best ? best.avoidedPayout : 0,
          }
        })
        .sort((a, b) => b.avoidedPayout - a.avoidedPayout),
    [losses.rows, properties],
  )

  const totalAvoidedPayout = upgradeSummaries.reduce((total, item) => total + item.avoidedPayout, 0)
  const propertiesThatBenefit = upgradeSummaries.filter((item) => item.best !== null).length
  const upgradesConsidered = new Set(losses.rows.map((row) => upgradeLabel(row))).size

  const tabs: [AnalysisTab, string][] = [
    ['overview', 'Overview'],
    ['storms', 'Storms'],
    ['properties', 'Properties'],
    ['upgrades', 'Upgrades'],
    ['year', 'Average year'],
    ['details', 'Details'],
  ]

  // Per property, the average-year row and the upgrade that saves the most repair cost per year.
  const yearRows = (averageYear?.properties ?? [])
    .map((row) => {
      const property = properties.find((p) => String(p.id) === row.property_id)
      const upgrades = Object.entries(row.upgrades)
        .map(([upgradeId, u]) => ({
          label: upgradeLabel({ upgrade_id: upgradeId, features_added: u.features_added }),
          featuresAdded: u.features_added?.length ?? 1,
          repair: u.expected_annual_avoided_repair_cost_usd,
          claims: u.expected_annual_avoided_payout_usd,
        }))
        .sort((a, b) => b.repair - a.repair || a.featuresAdded - b.featuresAdded)
      const best = upgrades.length > 0 && upgrades[0].repair > 0 ? upgrades[0] : null

      return { row, property, best }
    })
    .filter((item) => item.property !== undefined)
    .sort((a, b) => b.row.expected_annual_repair_cost_usd - a.row.expected_annual_repair_cost_usd)

  const stormCount = losses.storm_ids.length
  const propertyCount = properties.length

  return (
    <div className="analysis-backdrop">
      <div className="analysis-panel analysis-panel-large">
        <div className="analysis-header">
          <div>
            <span className="analysis-eyebrow">STORMSHIELD STORM RISK</span>
            <h2>Portfolio Risk Analysis</h2>
            <p>
              {stormCount} {stormCount === 1 ? 'storm' : 'storms'} · {propertyCount}{' '}
              {propertyCount === 1 ? 'property' : 'properties'} · {formatCurrency(portfolioValue)} of property
            </p>
          </div>

          <button className="analysis-close" type="button" onClick={onClose} aria-label="Close analysis">
            ×
          </button>
        </div>

        <div className="analysis-notice">
          <strong>Estimates for the storms you simulated</strong>
          <span>
            They show what these particular storms would do to these properties. They are not a
            prediction of what a year will bring.
          </span>
        </div>

        <div className="analysis-tabs">
          {tabs.map(([tab, label]) => (
            <button
              key={tab}
              type="button"
              className={activeTab === tab ? 'analysis-tab active' : 'analysis-tab'}
              onClick={() => setActiveTab(tab)}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="analysis-content">
          {/* ---------------- Overview ---------------- */}
          {activeTab === 'overview' && (
            <>
              <div className="analysis-metrics">
                <div className="analysis-metric">
                  <span>Storms simulated</span>
                  <strong>{stormCount}</strong>
                </div>

                <div className="analysis-metric">
                  <span>Storms that caused damage</span>
                  <strong>{damagingStorms.length}</strong>
                </div>

                <div className="analysis-metric">
                  <span>Properties damaged</span>
                  <strong>
                    {damagedPropertyIds.size} / {propertyCount}
                  </strong>
                </div>

                <div className="analysis-metric">
                  <span>Strongest wind at a property</span>
                  <strong>{peakGust.toFixed(1)} mph</strong>
                </div>

                <div className="analysis-metric">
                  <span>Worst storm: repair cost</span>
                  <strong>{formatCurrency(worstStormByDamage?.damage ?? 0)}</strong>
                  <small>{worstStormByDamage?.stormId ?? 'no damage'}</small>
                </div>

                <div className="analysis-metric">
                  <span>Worst storm: covered by insurance</span>
                  <strong>{formatCurrency(worstStormByPayout?.payout ?? 0)}</strong>
                  <small>{worstStormByPayout?.stormId ?? '—'}</small>
                </div>
              </div>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>REPAIR COST BY STORM</span>
                    <h3>Which storms do the damage</h3>
                  </div>
                  <p>Only storms that damaged at least one property</p>
                </div>

                <div className="analysis-chart-card">
                  {damagingStorms.length > 0 ? (
                    <ResponsiveContainer width="100%" height={320}>
                      <BarChart data={damagingStorms} margin={{ top: 15, right: 25, left: 15, bottom: 5 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} />
                        <XAxis dataKey="stormId" tickLine={false} />
                        <YAxis tickFormatter={formatCompactCurrency} tickLine={false} axisLine={false} />
                        <Tooltip formatter={(value) => formatCurrency(Number(value))} />
                        <Bar dataKey="damage" name="Repair cost" fill="#2f80ed" radius={[6, 6, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="analysis-empty-state">
                      None of the simulated storms damaged the selected properties.
                    </div>
                  )}
                </div>
              </section>
            </>
          )}

          {/* ---------------- Storms ---------------- */}
          {activeTab === 'storms' && (
            <>
              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>STORM BY STORM</span>
                    <h3>Repair cost and insurance cover</h3>
                  </div>
                  <p>Each storm on its own</p>
                </div>

                <div className="analysis-chart-card">
                  <ResponsiveContainer width="100%" height={340}>
                    <BarChart data={stormSummaries} margin={{ top: 15, right: 25, left: 15, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="stormId" tickLine={false} />
                      <YAxis tickFormatter={formatCompactCurrency} tickLine={false} axisLine={false} />
                      <Tooltip formatter={(value) => formatCurrency(Number(value))} />
                      <Legend />
                      <Bar dataKey="damage" name="Repair cost" fill="#2f80ed" radius={[5, 5, 0, 0]} />
                      <Bar dataKey="payout" name="Covered by insurance" fill="#0b2748" radius={[5, 5, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>THE NUMBERS</span>
                    <h3>Storm detail</h3>
                  </div>
                </div>

                <div className="analysis-table-wrapper">
                  <table className="analysis-table">
                    <thead>
                      <tr>
                        <th>Storm</th>
                        <th>Strongest wind</th>
                        <th>Properties damaged</th>
                        <th>Repair cost</th>
                        <th>Covered by insurance</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stormSummaries.map((storm) => (
                        <tr key={storm.stormId}>
                          <td>
                            <strong>{storm.stormId}</strong>
                          </td>
                          <td>{storm.peakGust.toFixed(1)} mph</td>
                          <td>
                            {storm.affectedProperties} / {propertyCount}
                          </td>
                          <td>{formatCurrency(storm.damage)}</td>
                          <td>{formatCurrency(storm.payout)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}

          {/* ---------------- Properties ---------------- */}
          {activeTab === 'properties' && (
            <>
              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>WHERE THE RISK SITS</span>
                    <h3>Worst storm for each property</h3>
                  </div>
                  <p>Which properties take the biggest hit</p>
                </div>

                <div className="analysis-chart-card">
                  <ResponsiveContainer width="100%" height={Math.max(260, propertyCount * 70)}>
                    <BarChart
                      data={propertySummaries.map((item) => ({ ...item, address: item.property.address }))}
                      layout="vertical"
                      margin={{ top: 10, right: 35, left: 25, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" tickFormatter={formatCompactCurrency} tickLine={false} axisLine={false} />
                      <YAxis type="category" dataKey="address" width={145} tickLine={false} />
                      <Tooltip formatter={(value) => formatCurrency(Number(value))} />
                      <Bar dataKey="worstDamage" name="Repair cost, worst storm" fill="#2f80ed" radius={[0, 6, 6, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>PROPERTY DETAIL</span>
                    <h3>Worst storm, property by property</h3>
                  </div>
                </div>

                <div className="analysis-table-wrapper">
                  <table className="analysis-table">
                    <thead>
                      <tr>
                        <th>Property</th>
                        <th>Worst storm</th>
                        <th>Strongest wind</th>
                        <th>Repair cost</th>
                        <th>Covered by insurance</th>
                      </tr>
                    </thead>
                    <tbody>
                      {propertySummaries.map(({ property, ...item }) => (
                        <tr key={property.id}>
                          <td>
                            <strong>{property.address}</strong>
                            <span>{property.city}, FL · {buildLabel(property)}</span>
                          </td>
                          <td>{item.worstStormId}</td>
                          <td>{item.peakGust.toFixed(1)} mph</td>
                          <td>{formatCurrency(item.worstDamage)}</td>
                          <td>{formatCurrency(item.worstPayout)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}

          {/* ---------------- Upgrades ---------------- */}
          {activeTab === 'upgrades' && (
            <>
              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>UPGRADES</span>
                    <h3>What an upgrade would save</h3>
                  </div>
                  <p>Insurance claims avoided across these storms, best upgrade per property</p>
                </div>

                <div className="analysis-metrics">
                  <div className="analysis-metric analysis-metric-accent">
                    <span>Claims avoided with upgrades</span>
                    <strong>{formatCurrency(totalAvoidedPayout)}</strong>
                  </div>

                  <div className="analysis-metric">
                    <span>Properties that would benefit</span>
                    <strong>
                      {propertiesThatBenefit} / {propertyCount}
                    </strong>
                  </div>

                  <div className="analysis-metric">
                    <span>Upgrades considered</span>
                    <strong>{upgradesConsidered}</strong>
                  </div>
                </div>

                <div className="analysis-chart-card">
                  <ResponsiveContainer width="100%" height={Math.max(280, propertyCount * 80)}>
                    <BarChart data={upgradeSummaries} layout="vertical" margin={{ top: 15, right: 35, left: 25, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" tickFormatter={formatCompactCurrency} tickLine={false} axisLine={false} />
                      <YAxis type="category" dataKey="address" width={145} tickLine={false} />
                      <Tooltip formatter={(value) => formatCurrency(Number(value))} />
                      <Legend />
                      <Bar dataKey="baselinePayout" name="Covered by insurance, as is" fill="#0b2748" radius={[0, 5, 5, 0]} />
                      <Bar dataKey="upgradedPayout" name="With the best upgrade" fill="#2f80ed" radius={[0, 5, 5, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>UPGRADE DETAIL</span>
                    <h3>Property by property</h3>
                  </div>
                  <p>Every upgrade the home could get, with what it would save</p>
                </div>

                <div className="analysis-table-wrapper">
                  <table className="analysis-table">
                    <thead>
                      <tr>
                        <th>Property</th>
                        <th>Best upgrade</th>
                        <th>Claims as is</th>
                        <th>With upgrade</th>
                        <th>Saved</th>
                      </tr>
                    </thead>
                    <tbody>
                      {upgradeSummaries.map(({ property, installed, best, candidates, ...item }) => (
                        <tr key={property.id}>
                          <td>
                            <strong>{property.address}</strong>
                            <span>
                              {buildLabel(property)}
                              {installed.length > 0 && ` · already has ${featureLabel(installed).toLowerCase()}`}
                            </span>
                          </td>
                          <td>
                            {best ? best.label : <span className="analysis-muted">No upgrade changes the outcome</span>}
                            {candidates.length > 1 && best && (
                              <span className="analysis-breakdown">
                                {candidates.map((c) => `${c.label} ${formatCurrency(c.avoidedPayout)}`).join(' · ')}
                              </span>
                            )}
                          </td>
                          <td>{formatCurrency(item.baselinePayout)}</td>
                          <td>{formatCurrency(item.upgradedPayout)}</td>
                          <td className="analysis-savings">{best ? formatCurrency(item.avoidedPayout) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="analysis-callout">
                  <strong>What this does not include yet</strong>
                  <p>
                    Upgrade prices, how often storms like these happen, and financing. Those decide whether an
                    upgrade pays for itself. The Insurer Lab explores that side with a sample insurer.
                  </p>
                </div>
              </section>
            </>
          )}

          {/* ---------------- Average year ---------------- */}
          {activeTab === 'year' && (
            <>
              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>AN AVERAGE YEAR</span>
                    <h3>What storms cost per year, on average</h3>
                  </div>
                  <p>
                    {averageYear
                      ? `${averageYear.sample_storms.toLocaleString()} simulated storms, ${averageYear.storms_per_year} a year`
                      : 'From thousands of simulated storms'}
                  </p>
                </div>

                {averageYearError && <div className="analysis-empty-state">{averageYearError}</div>}
                {!averageYear && !averageYearError && <div className="analysis-empty-state">Working it out…</div>}

                {averageYear && (
                  <>
                    <div className="analysis-metrics">
                      <div className="analysis-metric">
                        <span>Repair cost per year, all properties</span>
                        <strong>{formatCurrency(averageYear.totals.expected_annual_repair_cost_usd)}</strong>
                      </div>
                      <div className="analysis-metric">
                        <span>Covered by insurance per year</span>
                        <strong>{formatCurrency(averageYear.totals.expected_annual_payout_usd)}</strong>
                      </div>
                      <div className="analysis-metric">
                        <span>Storms a year in the record</span>
                        <strong>{averageYear.storms_per_year}</strong>
                        <small>
                          {averageYear.storms_per_year_basis.recent.from_year}–{averageYear.storms_per_year_basis.recent.to_year}
                        </small>
                      </div>
                    </div>

                    <div className="analysis-table-wrapper">
                      <table className="analysis-table">
                        <thead>
                          <tr>
                            <th>Property</th>
                            <th>Chance of storm damage in a year</th>
                            <th>Repair cost per year</th>
                            <th>Covered by insurance per year</th>
                            <th>Once-in-50-years repair cost</th>
                            <th>Best upgrade, repair cost saved per year</th>
                          </tr>
                        </thead>
                        <tbody>
                          {yearRows.map(({ row, property, best }) => (
                            <tr key={row.property_id}>
                              <td>
                                <strong>{property!.address}</strong>
                                <span>{property!.city}, FL · {buildLabel(property!)}</span>
                              </td>
                              <td>{pct(row.probability_of_damage_in_a_year)}</td>
                              <td>{formatCurrency(row.expected_annual_repair_cost_usd)}</td>
                              <td>{formatCurrency(row.expected_annual_payout_usd)}</td>
                              <td>
                                {row.return_periods_repair_cost.once_per_50_years_usd != null
                                  ? formatCurrency(row.return_periods_repair_cost.once_per_50_years_usd)
                                  : '—'}
                              </td>
                              <td>
                                {best ? (
                                  <>
                                    {best.label}
                                    <span className="analysis-breakdown">
                                      saves {formatCurrency(best.repair)} a year in repairs, {formatCurrency(best.claims)} of it insurance claims
                                    </span>
                                  </>
                                ) : (
                                  <span className="analysis-muted">No upgrade changes the outcome</span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    <div className="analysis-callout">
                      <strong>How to read this</strong>
                      <p>
                        These are averages over {averageYear.sample_storms.toLocaleString()} randomly generated Atlantic storms,
                        most of which never come near Florida, times how many storms a year the historical record holds. Most years
                        cost nothing; a bad year costs far more than the average. The once-in-50-years column shows the size of a
                        bad one.
                      </p>
                    </div>
                  </>
                )}
              </section>
            </>
          )}

          {/* ---------------- Details ---------------- */}
          {activeTab === 'details' && (
            <>
              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>HOW THESE NUMBERS ARE MADE</span>
                    <h3>In short</h3>
                  </div>
                </div>

                <ul className="analysis-detail-list">
                  <li>
                    Each storm's track gives a peak wind at every property. Published damage curves (FEMA
                    Hazus) turn that wind into a share of the building's value that would need repair,
                    depending on whether the home was built before or after Florida's 2002 building code,
                    its roof shape, and which storm protections it has.
                  </li>
                  <li>
                    "Covered by insurance" is the repair cost above a 5% deductible, up to the home's value.
                    The rest is paid by the owner.
                  </li>
                  <li>
                    Each storm is counted on its own, as if the home were fully repaired before the next
                    one. Nothing here says how likely any of these storms is.
                  </li>
                </ul>

                <div className="model-detail-grid">
                  <div>
                    <span>Run</span>
                    <strong>{losses.run_id}</strong>
                  </div>
                  <div>
                    <span>Storm set</span>
                    <strong>{losses.catalog_id}</strong>
                  </div>
                  <div>
                    <span>Damage data</span>
                    <strong>{losses.evidence_status === 'sourced' ? 'Published (FEMA Hazus)' : 'Placeholder assumptions'}</strong>
                  </div>
                  <div>
                    <span>Format</span>
                    <strong>v{losses.schema_version}</strong>
                  </div>
                </div>
              </section>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>FINE PRINT</span>
                    <h3>Assumptions</h3>
                  </div>
                </div>

                {losses.assumptions.length > 0 ? (
                  <ul className="analysis-detail-list">
                    {losses.assumptions.map((assumption, index) => (
                      <li key={index}>{assumption}</li>
                    ))}
                  </ul>
                ) : (
                  <p>None reported for this run.</p>
                )}
              </section>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>CAVEATS</span>
                    <h3>Things to know about this run</h3>
                  </div>
                </div>

                {losses.warnings.length > 0 ? (
                  <ul className="analysis-detail-list analysis-warning-list">
                    {losses.warnings.map((warning, index) => (
                      <li key={index}>{warning}</li>
                    ))}
                  </ul>
                ) : (
                  <p>Nothing flagged for this run.</p>
                )}
              </section>
            </>
          )}
        </div>

        <div className="analysis-footer">
          <span>Each storm counted on its own</span>
          <span>Estimates, not a forecast</span>
          <span>Covered by insurance = repair cost above the deductible, up to the home's value</span>
        </div>
      </div>
    </div>
  )
}

export default FullAnalysis
