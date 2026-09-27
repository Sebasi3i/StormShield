import type { FloridaStormBatch } from '../types/Storm'

interface BatchSettingsProps {
  seed: number
  maxWindKt: number
  onSeedChange: (seed: number) => void
  onMaxWindKtChange: (maxWindKt: number) => void
  disabled: boolean
  generating: boolean
  error: string | null
  // The batch last generated, so the row can say whether Simulate will reuse
  // it or search again.
  batch: FloridaStormBatch | null
}

const MAX_SEED = 2 ** 32 - 1

function formatCoordinate(value: number, positive: string, negative: string) {
  return `${Math.abs(value).toFixed(1)}°${value >= 0 ? positive : negative}`
}

/*
 * The generator's settings, shown under Storm Scenario when the Florida batch
 * is the selected scenario. Simulate does the generating.
 */
function BatchSettings({
  seed,
  maxWindKt,
  onSeedChange,
  onMaxWindKtChange,
  disabled,
  generating,
  error,
  batch,
}: BatchSettingsProps) {
  const current =
    batch !== null &&
    batch.generator.seed === seed &&
    batch.generator.start.max_wind_kt === maxWindKt

  return (
    <div className="batch-settings">
      <div className="batch-fields">
        <label>
          Seed
          <input
            type="number"
            min={0}
            max={MAX_SEED}
            value={seed}
            onChange={(event) => onSeedChange(Number(event.target.value))}
            disabled={disabled}
          />
        </label>

        <label>
          Start wind (kt)
          <input
            type="number"
            min={20}
            max={185}
            step={5}
            value={maxWindKt}
            onChange={(event) =>
              onMaxWindKtChange(Number(event.target.value))
            }
            disabled={disabled}
          />
        </label>

        <button
          type="button"
          className="batch-new-seed"
          onClick={() => onSeedChange(Math.floor(Math.random() * 100000))}
          disabled={disabled}
          title="Pick a new random seed"
        >
          New seed
        </button>
      </div>

      <p className="batch-hint">
        {generating
          ? 'Trying random starting points until at least 2 of 10 storms cross Florida at Cat 3+…'
          : error
            ? error
            : current && batch
              ? `${batch.storms.length} storms from ${formatCoordinate(
                  batch.generator.start.latitude,
                  'N',
                  'S',
                )}, ${formatCoordinate(
                  batch.generator.start.longitude,
                  'E',
                  'W',
                )} on ${batch.generator.start.date} · ${
                  batch.florida.hits.length
                } cross Florida at Cat 3+ · ${batch.florida.starts_tried} starts tried`
              : 'Simulate draws a random starting point, runs 10 storms from it and overlays them all. Same seed, same batch.'}
      </p>
    </div>
  )
}

export default BatchSettings
