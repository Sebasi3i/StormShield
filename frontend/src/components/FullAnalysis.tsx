import { useMemo, useState } from 'react'
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

import type { Property } from '../types/Property'
import type {
  StormLossResponse,
  StormLossRow,
} from '../types/StormLoss'

interface FullAnalysisProps {
  properties: Property[]
  losses: StormLossResponse
  onClose: () => void
}

type AnalysisTab =
  | 'overview'
  | 'storms'
  | 'properties'
  | 'mitigation'
  | 'model'

interface StormSummary {
  stormId: string
  damage: number
  payout: number
  peakGust: number
  affectedProperties: number
}

interface PropertySummary {
  propertyId: string
  address: string
  city: string
  peakGust: number
  worstStormId: string
  worstDamage: number
  worstPayout: number
}

interface MitigationSummary {
  propertyId: string
  address: string
  city: string
  upgradeId: string
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

function formatUpgradeName(upgradeId: string) {
  return upgradeId
    .split('_')
    .map(
      (word) =>
        word.charAt(0).toUpperCase() +
        word.slice(1),
    )
    .join(' ')
}

function getBaselineRows(
  rows: StormLossRow[],
): StormLossRow[] {
  return Array.from(
    new Map(
      rows.map((row) => [
        `${row.storm_id}:${row.property_id}`,
        row,
      ]),
    ).values(),
  )
}

function FullAnalysis({
  properties,
  losses,
  onClose,
}: FullAnalysisProps) {
  const [activeTab, setActiveTab] =
    useState<AnalysisTab>('overview')

  const baselineRows = useMemo(
    () => getBaselineRows(losses.rows),
    [losses.rows],
  )

  const portfolioValue = properties.reduce(
    (total, property) =>
      total + property.value,
    0,
  )

  /*
   * STORM DATA
   *
   * Used only by Overview and Storm Comparison.
   */
  const stormSummaries = useMemo(() => {
    return losses.storm_ids
      .map((stormId): StormSummary => {
        const rows = baselineRows.filter(
          (row) => row.storm_id === stormId,
        )

        return {
          stormId,

          damage: rows.reduce(
            (total, row) =>
              total +
              row.baseline_damage_usd,
            0,
          ),

          payout: rows.reduce(
            (total, row) =>
              total +
              row.baseline_payout_usd,
            0,
          ),

          peakGust:
            rows.length > 0
              ? Math.max(
                  ...rows.map(
                    (row) =>
                      row.peak_gust_mph,
                  ),
                )
              : 0,

          affectedProperties: rows.filter(
            (row) =>
              row.baseline_damage_usd > 0,
          ).length,
        }
      })
      .sort(
        (a, b) =>
          b.damage - a.damage,
      )
  }, [baselineRows, losses.storm_ids])

  const damagingStorms =
    stormSummaries.filter(
      (storm) => storm.damage > 0,
    )

  const largestDamageStorm =
    stormSummaries[0] ?? null

  const largestPayoutStorm =
    stormSummaries.reduce<
      StormSummary | null
    >((largest, storm) => {
      if (
        !largest ||
        storm.payout > largest.payout
      ) {
        return storm
      }

      return largest
    }, null)

  const peakGust =
    baselineRows.length > 0
      ? Math.max(
          ...baselineRows.map(
            (row) => row.peak_gust_mph,
          ),
        )
      : 0

  const affectedPropertyIds = new Set(
    baselineRows
      .filter(
        (row) =>
          row.baseline_damage_usd > 0,
      )
      .map((row) => row.property_id),
  )

  /*
   * PROPERTY DATA
   *
   * One row per property based on that
   * property's worst modeled event.
   */
  const propertySummaries = useMemo(() => {
    return properties
      .map(
        (property): PropertySummary => {
          const rows =
            baselineRows.filter(
              (row) =>
                row.property_id ===
                String(property.id),
            )

          const worstRow =
            rows.reduce<
              StormLossRow | null
            >((worst, row) => {
              if (
                !worst ||
                row.baseline_damage_usd >
                  worst.baseline_damage_usd
              ) {
                return row
              }

              return worst
            }, null)

          return {
            propertyId: String(
              property.id,
            ),
            address: property.address,
            city: property.city,

            peakGust:
              rows.length > 0
                ? Math.max(
                    ...rows.map(
                      (row) =>
                        row.peak_gust_mph,
                    ),
                  )
                : 0,

            worstStormId:
              worstRow?.storm_id ?? '—',

            worstDamage:
              worstRow?.baseline_damage_usd ??
              0,

            worstPayout:
              worstRow?.baseline_payout_usd ??
              0,
          }
        },
      )
      .sort(
        (a, b) =>
          b.worstDamage -
          a.worstDamage,
      )
  }, [baselineRows, properties])

  /*
   * MITIGATION DATA
   *
   * Choose ONE upgrade per property.
   *
   * For each available upgrade, sum its
   * baseline and upgraded payout across
   * the displayed scenario set.
   *
   * Then choose the upgrade producing
   * the greatest modeled avoided payout.
   */
  const mitigationSummaries =
    useMemo(() => {
      return properties
        .map(
          (
            property,
          ): MitigationSummary => {
            const propertyRows =
              losses.rows.filter(
                (row) =>
                  row.property_id ===
                  String(property.id),
              )

            const upgradeIds = Array.from(
              new Set(
                propertyRows.map(
                  (row) =>
                    row.upgrade_id,
                ),
              ),
            )

            const candidates =
              upgradeIds.map(
                (upgradeId) => {
                  const rows =
                    propertyRows.filter(
                      (row) =>
                        row.upgrade_id ===
                        upgradeId,
                    )

                  const baselinePayout =
                    rows.reduce(
                      (total, row) =>
                        total +
                        row.baseline_payout_usd,
                      0,
                    )

                  const upgradedPayout =
                    rows.reduce(
                      (total, row) =>
                        total +
                        row.upgraded_payout_usd,
                      0,
                    )

                  return {
                    upgradeId,
                    baselinePayout,
                    upgradedPayout,
                    avoidedPayout:
                      baselinePayout -
                      upgradedPayout,
                  }
                },
              )

            const best =
              candidates.reduce<
                | (typeof candidates)[number]
                | null
              >((currentBest, candidate) => {
                if (
                  !currentBest ||
                  candidate.avoidedPayout >
                    currentBest.avoidedPayout
                ) {
                  return candidate
                }

                return currentBest
              }, null)

            return {
              propertyId: String(
                property.id,
              ),
              address: property.address,
              city: property.city,
              upgradeId:
                best?.upgradeId ?? '',
              baselinePayout:
                best?.baselinePayout ?? 0,
              upgradedPayout:
                best?.upgradedPayout ?? 0,
              avoidedPayout:
                best?.avoidedPayout ?? 0,
            }
          },
        )
        .sort(
          (a, b) =>
            b.avoidedPayout -
            a.avoidedPayout,
        )
    }, [losses.rows, properties])

  const totalAvoidedPayout =
    mitigationSummaries.reduce(
      (total, property) =>
        total +
        property.avoidedPayout,
      0,
    )

  const mitigationOpportunities =
    mitigationSummaries.filter(
      (property) =>
        property.avoidedPayout > 0,
    ).length

  const damagingStormCount =
    damagingStorms.length

  return (
    <div className="analysis-backdrop">
      <div className="analysis-panel analysis-panel-large">
        <div className="analysis-header">
          <div>
            <span className="analysis-eyebrow">
              STORMSHIELD PORTFOLIO RISK
            </span>

            <h2>
              Portfolio Risk Analysis
            </h2>

            <p>
              {losses.storm_ids.length}{' '}
              {losses.storm_ids.length === 1
                ? 'storm'
                : 'storms'}{' '}
              · {properties.length}{' '}
              {properties.length === 1
                ? 'property'
                : 'properties'}{' '}
              ·{' '}
              {formatCurrency(
                portfolioValue,
              )}{' '}
              portfolio value
            </p>
          </div>

          <button
            className="analysis-close"
            type="button"
            onClick={onClose}
            aria-label="Close analysis"
          >
            ×
          </button>
        </div>

        <div className="analysis-notice">
          <strong>
            Illustrative estimate — includes
            assumptions
          </strong>

          <span>
            Results compare modeled synthetic
            storm scenarios. They are not an
            annual loss forecast.
          </span>
        </div>

        <div className="analysis-tabs">
          <button
            type="button"
            className={
              activeTab === 'overview'
                ? 'analysis-tab active'
                : 'analysis-tab'
            }
            onClick={() =>
              setActiveTab('overview')
            }
          >
            Overview
          </button>

          <button
            type="button"
            className={
              activeTab === 'storms'
                ? 'analysis-tab active'
                : 'analysis-tab'
            }
            onClick={() =>
              setActiveTab('storms')
            }
          >
            Storm Comparison
          </button>

          <button
            type="button"
            className={
              activeTab === 'properties'
                ? 'analysis-tab active'
                : 'analysis-tab'
            }
            onClick={() =>
              setActiveTab('properties')
            }
          >
            Property Impact
          </button>

          <button
            type="button"
            className={
              activeTab === 'mitigation'
                ? 'analysis-tab active'
                : 'analysis-tab'
            }
            onClick={() =>
              setActiveTab('mitigation')
            }
          >
            Mitigation
          </button>

          <button
            type="button"
            className={
              activeTab === 'model'
                ? 'analysis-tab active'
                : 'analysis-tab'
            }
            onClick={() =>
              setActiveTab('model')
            }
          >
            Model Details
          </button>
        </div>

        <div className="analysis-content">
          {/* =========================
              OVERVIEW
              ========================= */}

          {activeTab === 'overview' && (
            <>
              <div className="analysis-metrics">
                <div className="analysis-metric">
                  <span>
                    Storms simulated
                  </span>
                  <strong>
                    {losses.storm_ids.length}
                  </strong>
                </div>

                <div className="analysis-metric">
                  <span>
                    Damaging scenarios
                  </span>
                  <strong>
                    {damagingStormCount}
                  </strong>
                </div>

                <div className="analysis-metric">
                  <span>
                    Properties affected
                  </span>
                  <strong>
                    {
                      affectedPropertyIds.size
                    }{' '}
                    / {properties.length}
                  </strong>
                </div>

                <div className="analysis-metric">
                  <span>
                    Highest property gust
                  </span>
                  <strong>
                    {peakGust.toFixed(1)} mph
                  </strong>
                </div>

                <div className="analysis-metric">
                  <span>
                    Largest event damage
                  </span>
                  <strong>
                    {formatCurrency(
                      largestDamageStorm?.damage ??
                        0,
                    )}
                  </strong>
                  <small>
                    {largestDamageStorm?.stormId ??
                      '—'}
                  </small>
                </div>

                <div className="analysis-metric">
                  <span>
                    Largest event payout
                  </span>
                  <strong>
                    {formatCurrency(
                      largestPayoutStorm?.payout ??
                        0,
                    )}
                  </strong>
                  <small>
                    {largestPayoutStorm?.stormId ??
                      '—'}
                  </small>
                </div>
              </div>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>
                      PORTFOLIO EXPOSURE
                    </span>

                    <h3>
                      Damage-Producing
                      Scenarios
                    </h3>
                  </div>

                  <p>
                    Modeled building damage by
                    storm
                  </p>
                </div>

                <div className="analysis-chart-card">
                  {damagingStorms.length >
                  0 ? (
                    <ResponsiveContainer
                      width="100%"
                      height={320}
                    >
                      <BarChart
                        data={damagingStorms}
                        margin={{
                          top: 15,
                          right: 25,
                          left: 15,
                          bottom: 5,
                        }}
                      >
                        <CartesianGrid
                          strokeDasharray="3 3"
                          vertical={false}
                        />

                        <XAxis
                          dataKey="stormId"
                          tickLine={false}
                        />

                        <YAxis
                          tickFormatter={
                            formatCompactCurrency
                          }
                          tickLine={false}
                          axisLine={false}
                        />

                        <Tooltip
                          formatter={(
                            value,
                          ) =>
                            formatCurrency(
                              Number(value),
                            )
                          }
                        />

                        <Bar
                          dataKey="damage"
                          name="Building Damage"
                          fill="#2f80ed"
                          radius={[
                            6, 6, 0, 0,
                          ]}
                        />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="analysis-empty-state">
                      No modeled building damage
                      occurred in the selected
                      scenarios.
                    </div>
                  )}
                </div>
              </section>
            </>
          )}

          {/* =========================
              STORM COMPARISON
              ========================= */}

          {activeTab === 'storms' && (
            <>
              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>
                      SCENARIO COMPARISON
                    </span>

                    <h3>
                      Damage vs Insurer Payout
                    </h3>
                  </div>

                  <p>
                    Independent-event modeled
                    outcomes
                  </p>
                </div>

                <div className="analysis-chart-card">
                  <ResponsiveContainer
                    width="100%"
                    height={340}
                  >
                    <BarChart
                      data={stormSummaries}
                      margin={{
                        top: 15,
                        right: 25,
                        left: 15,
                        bottom: 5,
                      }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        vertical={false}
                      />

                      <XAxis
                        dataKey="stormId"
                        tickLine={false}
                      />

                      <YAxis
                        tickFormatter={
                          formatCompactCurrency
                        }
                        tickLine={false}
                        axisLine={false}
                      />

                      <Tooltip
                        formatter={(
                          value,
                        ) =>
                          formatCurrency(
                            Number(value),
                          )
                        }
                      />

                      <Legend />

                      <Bar
                        dataKey="damage"
                        name="Building Damage"
                        fill="#2f80ed"
                        radius={[
                          5, 5, 0, 0,
                        ]}
                      />

                      <Bar
                        dataKey="payout"
                        name="Insurer Payout"
                        fill="#0b2748"
                        radius={[
                          5, 5, 0, 0,
                        ]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>
                      EXACT RESULTS
                    </span>

                    <h3>
                      Scenario Detail
                    </h3>
                  </div>
                </div>

                <div className="analysis-table-wrapper">
                  <table className="analysis-table">
                    <thead>
                      <tr>
                        <th>Storm</th>
                        <th>
                          Peak Property Gust
                        </th>
                        <th>
                          Properties Affected
                        </th>
                        <th>
                          Building Damage
                        </th>
                        <th>
                          Insurer Payout
                        </th>
                      </tr>
                    </thead>

                    <tbody>
                      {stormSummaries.map(
                        (storm) => (
                          <tr
                            key={
                              storm.stormId
                            }
                          >
                            <td>
                              <strong>
                                {
                                  storm.stormId
                                }
                              </strong>
                            </td>

                            <td>
                              {storm.peakGust.toFixed(
                                1,
                              )}{' '}
                              mph
                            </td>

                            <td>
                              {
                                storm.affectedProperties
                              }{' '}
                              /{' '}
                              {
                                properties.length
                              }
                            </td>

                            <td>
                              {formatCurrency(
                                storm.damage,
                              )}
                            </td>

                            <td>
                              {formatCurrency(
                                storm.payout,
                              )}
                            </td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}

          {/* =========================
              PROPERTY IMPACT
              ========================= */}

          {activeTab === 'properties' && (
            <>
              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>
                      RISK CONCENTRATION
                    </span>

                    <h3>
                      Worst-Event Damage by
                      Property
                    </h3>
                  </div>

                  <p>
                    Which insured homes drive
                    modeled losses?
                  </p>
                </div>

                <div className="analysis-chart-card">
                  <ResponsiveContainer
                    width="100%"
                    height={Math.max(
                      260,
                      properties.length * 70,
                    )}
                  >
                    <BarChart
                      data={
                        propertySummaries
                      }
                      layout="vertical"
                      margin={{
                        top: 10,
                        right: 35,
                        left: 25,
                        bottom: 5,
                      }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        horizontal={false}
                      />

                      <XAxis
                        type="number"
                        tickFormatter={
                          formatCompactCurrency
                        }
                        tickLine={false}
                        axisLine={false}
                      />

                      <YAxis
                        type="category"
                        dataKey="address"
                        width={145}
                        tickLine={false}
                      />

                      <Tooltip
                        formatter={(
                          value,
                        ) =>
                          formatCurrency(
                            Number(value),
                          )
                        }
                      />

                      <Bar
                        dataKey="worstDamage"
                        name="Worst-Event Damage"
                        fill="#2f80ed"
                        radius={[
                          0, 6, 6, 0,
                        ]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>
                      PROPERTY DETAIL
                    </span>

                    <h3>
                      Worst Modeled Event
                    </h3>
                  </div>
                </div>

                <div className="analysis-table-wrapper">
                  <table className="analysis-table">
                    <thead>
                      <tr>
                        <th>Property</th>
                        <th>Worst Storm</th>
                        <th>Peak Gust</th>
                        <th>
                          Worst-Event Damage
                        </th>
                        <th>
                          Insurer Payout
                        </th>
                      </tr>
                    </thead>

                    <tbody>
                      {propertySummaries.map(
                        (property) => (
                          <tr
                            key={
                              property.propertyId
                            }
                          >
                            <td>
                              <strong>
                                {
                                  property.address
                                }
                              </strong>

                              <span>
                                {property.city},
                                FL
                              </span>
                            </td>

                            <td>
                              {
                                property.worstStormId
                              }
                            </td>

                            <td>
                              {property.peakGust.toFixed(
                                1,
                              )}{' '}
                              mph
                            </td>

                            <td>
                              {formatCurrency(
                                property.worstDamage,
                              )}
                            </td>

                            <td>
                              {formatCurrency(
                                property.worstPayout,
                              )}
                            </td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}

          {/* =========================
              MITIGATION
              ========================= */}

          {activeTab === 'mitigation' && (
            <>
              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>
                      MITIGATION EFFECT
                    </span>

                    <h3>
                      Baseline vs Upgraded
                      Insurer Payout
                    </h3>
                  </div>

                  <p>
                    One modeled upgrade selected
                    per property
                  </p>
                </div>

                <div className="analysis-metrics">
                  <div className="analysis-metric analysis-metric-accent">
                    <span>
                      Scenario-set avoided
                      payout
                    </span>

                    <strong>
                      {formatCurrency(
                        totalAvoidedPayout,
                      )}
                    </strong>
                  </div>

                  <div className="analysis-metric">
                    <span>
                      Mitigation opportunities
                    </span>

                    <strong>
                      {
                        mitigationOpportunities
                      }{' '}
                      / {properties.length}
                    </strong>
                  </div>

                  <div className="analysis-metric">
                    <span>
                      Upgrade options modeled
                    </span>

                    <strong>
                      {
                        new Set(
                          losses.rows.map(
                            (row) =>
                              row.upgrade_id,
                          ),
                        ).size
                      }
                    </strong>
                  </div>
                </div>

                <div className="analysis-chart-card">
                  <ResponsiveContainer
                    width="100%"
                    height={Math.max(
                      280,
                      properties.length * 80,
                    )}
                  >
                    <BarChart
                      data={
                        mitigationSummaries
                      }
                      layout="vertical"
                      margin={{
                        top: 15,
                        right: 35,
                        left: 25,
                        bottom: 5,
                      }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        horizontal={false}
                      />

                      <XAxis
                        type="number"
                        tickFormatter={
                          formatCompactCurrency
                        }
                        tickLine={false}
                        axisLine={false}
                      />

                      <YAxis
                        type="category"
                        dataKey="address"
                        width={145}
                        tickLine={false}
                      />

                      <Tooltip
                        formatter={(
                          value,
                        ) =>
                          formatCurrency(
                            Number(value),
                          )
                        }
                      />

                      <Legend />

                      <Bar
                        dataKey="baselinePayout"
                        name="Baseline Payout"
                        fill="#0b2748"
                        radius={[
                          0, 5, 5, 0,
                        ]}
                      />

                      <Bar
                        dataKey="upgradedPayout"
                        name="Upgraded Payout"
                        fill="#2f80ed"
                        radius={[
                          0, 5, 5, 0,
                        ]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>
                      UPGRADE EFFECTIVENESS
                    </span>

                    <h3>
                      Property Mitigation
                      Detail
                    </h3>
                  </div>
                </div>

                <div className="analysis-table-wrapper">
                  <table className="analysis-table">
                    <thead>
                      <tr>
                        <th>Property</th>
                        <th>
                          Selected Upgrade
                        </th>
                        <th>
                          Baseline Payout
                        </th>
                        <th>
                          Upgraded Payout
                        </th>
                        <th>
                          Avoided Payout
                        </th>
                      </tr>
                    </thead>

                    <tbody>
                      {mitigationSummaries.map(
                        (item) => (
                          <tr
                            key={
                              item.propertyId
                            }
                          >
                            <td>
                              <strong>
                                {item.address}
                              </strong>

                              <span>
                                {item.city}, FL
                              </span>
                            </td>

                            <td>
                              {item.upgradeId
                                ? formatUpgradeName(
                                    item.upgradeId,
                                  )
                                : '—'}
                            </td>

                            <td>
                              {formatCurrency(
                                item.baselinePayout,
                              )}
                            </td>

                            <td>
                              {formatCurrency(
                                item.upgradedPayout,
                              )}
                            </td>

                            <td className="analysis-savings">
                              {formatCurrency(
                                item.avoidedPayout,
                              )}
                            </td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                </div>

                <div className="analysis-callout">
                  <strong>
                    Investment analysis pending
                  </strong>

                  <p>
                    Annual event rates, upgrade
                    costs, insurer contributions
                    and finance assumptions are
                    needed before calculating
                    expected annual claim
                    savings, payback or NPV.
                  </p>
                </div>
              </section>
            </>
          )}

          {/* =========================
              MODEL DETAILS
              ========================= */}

          {activeTab === 'model' && (
            <>
              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>
                      MODEL INFORMATION
                    </span>

                    <h3>
                      Run & Evidence Details
                    </h3>
                  </div>
                </div>

                <div className="model-detail-grid">
                  <div>
                    <span>
                      Schema version
                    </span>
                    <strong>
                      {losses.schema_version}
                    </strong>
                  </div>

                  <div>
                    <span>Run ID</span>
                    <strong>
                      {losses.run_id}
                    </strong>
                  </div>

                  <div>
                    <span>Catalog ID</span>
                    <strong>
                      {losses.catalog_id}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Evidence status
                    </span>
                    <strong>
                      {
                        losses.evidence_status
                      }
                    </strong>
                  </div>
                </div>
              </section>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>
                      ASSUMPTIONS
                    </span>

                    <h3>
                      Modeling Assumptions
                    </h3>
                  </div>
                </div>

                {losses.assumptions.length >
                0 ? (
                  <ul className="analysis-detail-list">
                    {losses.assumptions.map(
                      (
                        assumption,
                        index,
                      ) => (
                        <li key={index}>
                          {assumption}
                        </li>
                      ),
                    )}
                  </ul>
                ) : (
                  <p>
                    No assumptions were returned
                    by this run.
                  </p>
                )}
              </section>

              <section className="analysis-section">
                <div className="analysis-section-heading">
                  <div>
                    <span>WARNINGS</span>

                    <h3>
                      Model Limitations
                    </h3>
                  </div>
                </div>

                {losses.warnings.length >
                0 ? (
                  <ul className="analysis-detail-list analysis-warning-list">
                    {losses.warnings.map(
                      (warning, index) => (
                        <li key={index}>
                          {warning}
                        </li>
                      ),
                    )}
                  </ul>
                ) : (
                  <p>
                    No warnings were returned by
                    this run.
                  </p>
                )}
              </section>
            </>
          )}
        </div>

        <div className="analysis-footer">
          <span>
            Independent-event payout
            approximation
          </span>

          <span>
            Before reinsurance, taxes, and
            capital effects
          </span>

          <span>
            Evidence status:{' '}
            {losses.evidence_status}
          </span>
        </div>
      </div>
    </div>
  )
}

export default FullAnalysis