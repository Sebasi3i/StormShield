import { useState } from 'react'
import type { FloridaStormBatch } from '../types/Storm'

export interface GenerationOptions {
  seed: number
  maxWindKt: number
}

interface GenerateStormsProps {
  onGenerate: (options: GenerationOptions) => void
  generating: boolean
  disabled: boolean
  error: string | null
  result: FloridaStormBatch | null
}

const BATCH_SIZE = 10
const MAX_SEED = 2 ** 32 - 1

function formatCoordinate(value: number, positive: string, negative: string) {
  return `${Math.abs(value).toFixed(1)}°${value >= 0 ? positive : negative}`
}

function GenerateStorms({
  onGenerate,
  generating,
  disabled,
  error,
  result,
}: GenerateStormsProps) {
  const [seed, setSeed] = useState(42)
  const [maxWindKt, setMaxWindKt] = useState(70)

  const busy = generating || disabled

  return (
    <section className="generate-storms">
      <span className="generate-label">STORM GENERATOR</span>

      <h2>Generate {BATCH_SIZE} Florida Storms</h2>

      <p className="generate-intro">
        Runs the hurricane simulator from a starting point drawn at random
        from the historical record, anywhere from the Cape Verde islands to
        the Gulf, and keeps the first batch in which at least two storms cross
        Florida at Category 3 or stronger. All {BATCH_SIZE} are simulated and
        priced together.
      </p>

      <div className="generate-fields">
        <label>
          Seed
          <input
            type="number"
            min={0}
            max={MAX_SEED}
            value={seed}
            onChange={(event) => setSeed(Number(event.target.value))}
            disabled={busy}
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
            onChange={(event) => setMaxWindKt(Number(event.target.value))}
            disabled={busy}
          />
        </label>
      </div>

      <div className="generate-actions">
        <button
          type="button"
          className="generate-button"
          onClick={() => onGenerate({ seed, maxWindKt })}
          disabled={busy}
        >
          {generating ? 'Searching…' : `Generate ${BATCH_SIZE} storms`}
        </button>

        <button
          type="button"
          className="reroll-button"
          onClick={() => setSeed(Math.floor(Math.random() * 100000))}
          disabled={busy}
          title="Pick a new random seed"
        >
          New seed
        </button>
      </div>

      {generating && (
        <p className="generate-note">
          Trying random starting points until enough storms reach Florida.
          This takes a few seconds; the first run also loads the simulator.
        </p>
      )}

      {error && (
        <p className="generate-error" role="alert">
          {error}
        </p>
      )}

      {result && !error && !generating && (
        <div className="generate-result">
          <strong>
            {result.storms.length} storms · {result.florida.hits.length} reach
            Florida as Cat 3+
          </strong>

          <span>
            Start{' '}
            {formatCoordinate(result.generator.start.latitude, 'N', 'S')},{' '}
            {formatCoordinate(result.generator.start.longitude, 'E', 'W')} on{' '}
            {result.generator.start.date} · {result.florida.starts_tried}{' '}
            {result.florida.starts_tried === 1 ? 'start' : 'starts'} tried ·
            seed {result.generator.seed}
          </span>

          <p>
            Choose “Florida batch” under Storm Scenario, then Simulate to run
            all {result.storms.length} together. Click a track to see that
            storm's details.
          </p>

          <p>{result.completeness_warning}</p>
        </div>
      )}
    </section>
  )
}

export default GenerateStorms
