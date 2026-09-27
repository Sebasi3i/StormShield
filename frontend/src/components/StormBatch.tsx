import type { Storm } from '../types/Storm'
import type { StormLossResponse } from '../types/StormLoss'

interface StormBatchProps {
  storms: Storm[]
  focusedStormId: string | null
  onFocusStorm: (stormId: string) => void
  // The batch's loss run, once it has completed; null while it is loading or
  // when no properties were selected.
  losses: StormLossResponse | null
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value)
}

/*
 * Baseline payout for one storm across the selected properties. Every upgrade
 * repeats the baseline on its own row, so keep one row per property.
 */
function baselinePayout(losses: StormLossResponse, stormId: string) {
  const perProperty = new Map<string, number>()

  losses.rows.forEach((row) => {
    if (row.storm_id === stormId) {
      perProperty.set(row.property_id, row.baseline_payout_usd)
    }
  })

  return Array.from(perProperty.values()).reduce(
    (total, payout) => total + payout,
    0,
  )
}

function StormBatch({
  storms,
  focusedStormId,
  onFocusStorm,
  losses,
}: StormBatchProps) {
  const hits = storms.filter((storm) => storm.florida_hit).length

  return (
    <section className="storm-batch">
      <span className="generate-label">FLORIDA BATCH</span>

      <h2>
        {storms.length} storms · {hits} reach Florida as Cat 3+
      </h2>

      <p className="generate-intro">
        One origin, {storms.length} paths. Select a storm here or click its
        track on the map for that storm's impact.
      </p>

      <ul className="storm-batch-list">
        {storms.map((storm) => {
          const focused = storm.storm_id === focusedStormId

          return (
            <li key={storm.storm_id}>
              <button
                type="button"
                className={
                  focused ? 'storm-batch-row focused' : 'storm-batch-row'
                }
                onClick={() => onFocusStorm(storm.storm_id)}
              >
                <span
                  className={
                    storm.florida_hit
                      ? 'storm-batch-dot hit'
                      : 'storm-batch-dot'
                  }
                />

                <span className="storm-batch-id">
                  <strong>{storm.storm_id}</strong>

                  <small>
                    peak {Math.round(storm.peak_wind_kt)} kt
                    {storm.florida_hit && storm.florida_peak_wind_kt != null
                      ? ` · ${Math.round(storm.florida_peak_wind_kt)} kt over Florida`
                      : storm.landfall
                        ? ' · landfall elsewhere'
                        : ' · no landfall'}
                  </small>
                </span>

                <span className="storm-batch-payout">
                  {losses ? (
                    <>
                      <strong>
                        {formatCurrency(baselinePayout(losses, storm.storm_id))}
                      </strong>

                      <small>payout</small>
                    </>
                  ) : (
                    <small>—</small>
                  )}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

export default StormBatch
