import type { Property } from '../types/Property'
import type {
  StormLossResponse,
  StormLossRow,
} from '../types/StormLoss'

interface StormImpactProps {
  properties: Property[]
  losses: StormLossResponse
  onViewAnalysis: () => void
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value)
}

function StormImpact({
  properties,
  losses,
  onViewAnalysis,
}: StormImpactProps) {
  if (losses.rows.length === 0) {
    return null
  }

  /*
   * Each property can have multiple rows because each upgrade gets
   * its own row. Baseline damage/payout repeats across those rows.
   *
   * We therefore keep one baseline row per property when calculating
   * portfolio totals.
   */
  const baselineRows = Array.from(
    new Map(
      losses.rows.map((row) => [
        row.property_id,
        row,
      ]),
    ).values(),
  )

  const totalDamage = baselineRows.reduce(
    (total, row) => total + row.baseline_damage_usd,
    0,
  )

  const totalPayout = baselineRows.reduce(
    (total, row) => total + row.baseline_payout_usd,
    0,
  )

  const peakGust = Math.max(
    ...baselineRows.map((row) => row.peak_gust_mph),
  )

  /*
   * For this selected-storm summary, show the largest avoided payout
   * available for each property.
   *
   * This is NOT yet the investment recommendation. It is simply the
   * best modeled payout reduction among the returned upgrade options
   * for this particular storm.
   */
  const bestUpgradeByProperty = new Map<
    string,
    StormLossRow
  >()

  losses.rows.forEach((row) => {
    const current = bestUpgradeByProperty.get(
      row.property_id,
    )

    if (
      !current ||
      row.avoided_payout_usd > current.avoided_payout_usd
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
    (total, row) => total + row.avoided_payout_usd,
    0,
  )

  const stormId = baselineRows[0].storm_id

  return (
    <div className="storm-impact">
      <div className="storm-impact-heading">
        <div>
          <span className="storm-impact-label">
            STORM IMPACT
          </span>

          <h2>Portfolio Impact</h2>

          <p>
            {stormId} · {properties.length}{' '}
            {properties.length === 1
              ? 'property'
              : 'properties'}
          </p>
        </div>

        <span className="impact-estimate-badge">
          Illustrative
        </span>
      </div>

      <div className="impact-gust">
        <span className="impact-gust-value">
          {peakGust.toFixed(1)}
        </span>

        <div>
          <span className="impact-gust-unit">
            mph
          </span>

          <p>Highest peak property gust</p>
        </div>
      </div>

      <div className="impact-financials">
        <div className="impact-stat">
          <span>Modeled building damage</span>

          <strong>
            {formatCurrency(totalDamage)}
          </strong>
        </div>

        <div className="impact-stat">
          <span>Modeled insurer payout</span>

          <strong>
            {formatCurrency(totalPayout)}
          </strong>
        </div>
      </div>

      <div className="impact-highlight">
        <div>
          <span>
            Selected-storm avoidable payout
          </span>

          <p>
            Best returned upgrade per property
          </p>
        </div>

        <strong>
          {formatCurrency(potentialAvoidedPayout)}
        </strong>
      </div>

      <div className="impact-divider" />

      <div className="property-impact-preview">
        <div className="mitigation-heading">
          Property Impact
        </div>

        {baselineRows
          .slice()
          .sort(
            (a, b) =>
              b.baseline_damage_usd -
              a.baseline_damage_usd,
          )
          .slice(0, 4)
          .map((row) => {
            const property = properties.find(
              (item) =>
                String(item.id) === row.property_id,
            )

            return (
              <div
                className="property-impact-row"
                key={row.property_id}
              >
                <div>
                  <strong>
                    {property?.address ??
                      `Property ${row.property_id}`}
                  </strong>

                  <span>
                    {row.peak_gust_mph.toFixed(1)} mph
                  </span>
                </div>

                <div className="property-impact-loss">
                  <strong>
                    {formatCurrency(
                      row.baseline_damage_usd,
                    )}
                  </strong>

                  <span>damage</span>
                </div>
              </div>
            )
          })}
      </div>

      <button
    className="full-analysis-button"
    type="button"
    onClick={onViewAnalysis}
    >
        View Full Analysis
        <span>→</span>
      </button>

      <div className="impact-disclaimer">
        Illustrative estimate — includes assumptions
      </div>
    </div>
  )
}

export default StormImpact