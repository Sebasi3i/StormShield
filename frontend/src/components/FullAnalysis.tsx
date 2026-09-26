import type { Property } from '../types/Property'
import type { StormLossResponse } from '../types/StormLoss'

interface FullAnalysisProps {
  properties: Property[]
  losses: StormLossResponse
  onClose: () => void
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value)
}

function formatUpgradeName(upgradeId: string) {
  return upgradeId
    .split('_')
    .map(
      (word) =>
        word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(' ')
}

function FullAnalysis({
  properties,
  losses,
  onClose,
}: FullAnalysisProps) {
  // Each upgrade creates another row for the same property.
  // Baseline values repeat, so only keep one baseline row
  // per property for portfolio totals.
  const baselineRows = Array.from(
    new Map(
      losses.rows.map((row) => [
        row.property_id,
        row,
      ]),
    ).values(),
  )

  const totalDamage = baselineRows.reduce(
    (total, row) =>
      total + row.baseline_damage_usd,
    0,
  )

  const totalPayout = baselineRows.reduce(
    (total, row) =>
      total + row.baseline_payout_usd,
    0,
  )

  const peakGust =
    baselineRows.length > 0
      ? Math.max(
          ...baselineRows.map(
            (row) => row.peak_gust_mph,
          ),
        )
      : 0

  const affectedProperties = baselineRows.filter(
    (row) => row.baseline_damage_usd > 0,
  ).length

  // Find the upgrade with the largest avoided payout
  // for each property for THIS selected storm.
  const bestUpgradeByProperty = new Map<
    string,
    (typeof losses.rows)[number]
  >()

  losses.rows.forEach((row) => {
    const current = bestUpgradeByProperty.get(
      row.property_id,
    )

    if (
      !current ||
      row.avoided_payout_usd >
        current.avoided_payout_usd
    ) {
      bestUpgradeByProperty.set(
        row.property_id,
        row,
      )
    }
  })

  const potentialAvoidedPayout = Array.from(
    bestUpgradeByProperty.values(),
  ).reduce(
    (total, row) =>
      total + row.avoided_payout_usd,
    0,
  )

  const stormId =
    baselineRows[0]?.storm_id ?? 'Unknown'

  return (
    <div className="analysis-backdrop">
      <div className="analysis-panel">
        <div className="analysis-header">
          <div>
            <span className="analysis-eyebrow">
              STORMSHIELD CATASTROPHE ANALYSIS
            </span>

            <h2>Storm Impact Analysis</h2>

            <p>
              {stormId} · {properties.length}{' '}
              {properties.length === 1
                ? 'property'
                : 'properties'}{' '}
              analyzed
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
            Illustrative estimate — includes assumptions
          </strong>

          <span>
            Property losses use the current
            StormShield demonstration wind and
            vulnerability models.
          </span>
        </div>

        <div className="analysis-metrics">
          <div className="analysis-metric">
            <span>Properties analyzed</span>
            <strong>{properties.length}</strong>
          </div>

          <div className="analysis-metric">
            <span>
              Properties with modeled damage
            </span>

            <strong>{affectedProperties}</strong>
          </div>

          <div className="analysis-metric">
            <span>
              Highest peak property gust
            </span>

            <strong>
              {peakGust.toFixed(1)} mph
            </strong>
          </div>

          <div className="analysis-metric">
            <span>
              Modeled building damage
            </span>

            <strong>
              {formatCurrency(totalDamage)}
            </strong>
          </div>

          <div className="analysis-metric">
            <span>
              Modeled insurer payout
            </span>

            <strong>
              {formatCurrency(totalPayout)}
            </strong>
          </div>

          <div className="analysis-metric analysis-metric-accent">
            <span>
              Selected-storm avoidable payout
            </span>

            <strong>
              {formatCurrency(
                potentialAvoidedPayout,
              )}
            </strong>
          </div>
        </div>

        <section className="analysis-section">
          <div className="analysis-section-heading">
            <div>
              <span>PROPERTY IMPACT</span>
              <h3>Portfolio Loss Detail</h3>
            </div>

            <p>
              Ranked by modeled building damage
            </p>
          </div>

          <div className="analysis-table-wrapper">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th>Property</th>
                  <th>Peak Gust</th>
                  <th>Building Damage</th>
                  <th>Insurer Payout</th>
                  <th>Best Event Upgrade</th>
                  <th>Avoided Payout</th>
                </tr>
              </thead>

              <tbody>
                {baselineRows
                  .slice()
                  .sort(
                    (a, b) =>
                      b.baseline_damage_usd -
                      a.baseline_damage_usd,
                  )
                  .map((row) => {
                    const property =
                      properties.find(
                        (item) =>
                          String(item.id) ===
                          row.property_id,
                      )

                    const bestUpgrade =
                      bestUpgradeByProperty.get(
                        row.property_id,
                      )

                    return (
                      <tr key={row.property_id}>
                        <td>
                          <strong>
                            {property?.address ??
                              `Property ${row.property_id}`}
                          </strong>

                          <span>
                            {property?.city
                              ? `${property.city}, FL`
                              : ''}
                          </span>
                        </td>

                        <td>
                          {row.peak_gust_mph.toFixed(
                            1,
                          )}{' '}
                          mph
                        </td>

                        <td>
                          {formatCurrency(
                            row.baseline_damage_usd,
                          )}
                        </td>

                        <td>
                          {formatCurrency(
                            row.baseline_payout_usd,
                          )}
                        </td>

                        <td>
                          {bestUpgrade
                            ? formatUpgradeName(
                                bestUpgrade.upgrade_id,
                              )
                            : '—'}
                        </td>

                        <td className="analysis-savings">
                          {bestUpgrade
                            ? formatCurrency(
                                bestUpgrade.avoided_payout_usd,
                              )
                            : '—'}
                        </td>
                      </tr>
                    )
                  })}
              </tbody>
            </table>
          </div>
        </section>

        <div className="analysis-footer">
          <span>
            Before reinsurance, taxes, and capital
            effects
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