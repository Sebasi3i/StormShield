import { useState } from 'react'
import type {
  GeneratedStormCatalog,
  GenerationStart,
} from '../types/Storm'

export interface GenerationOptions {
  maxWindKt: number
  startDate: string
  count: number
  seed: number
}

interface GenerateStormsProps {
  start: GenerationStart
  pickingStart: boolean
  onTogglePickStart: () => void
  onGenerate: (options: GenerationOptions) => void
  generating: boolean
  disabled: boolean
  error: string | null
  result: GeneratedStormCatalog | null
}

function formatCoordinate(value: number, positive: string, negative: string) {
  return `${Math.abs(value).toFixed(2)}°${value >= 0 ? positive : negative}`
}

function GenerateStorms({
  start,
  pickingStart,
  onTogglePickStart,
  onGenerate,
  generating,
  disabled,
  error,
  result,
}: GenerateStormsProps) {
  const [maxWindKt, setMaxWindKt] = useState(60)
  const [startDate, setStartDate] = useState(
    `${new Date().getFullYear()}-09-10`,
  )
  const [count, setCount] = useState(10)
  const [seed, setSeed] = useState(42)

  const busy = generating || disabled
  const landfalls = result
    ? result.storms.filter((storm) => storm.landfall).length
    : 0

  return (
    <section className="generate-storms">
      <span className="generate-label">STORM GENERATOR</span>

      <h2>Generate Storms</h2>

      <p className="generate-intro">
        Run the hurricane simulator from a starting point you choose. Every
        storm starts there; each follows its own seed.
      </p>

      <div className="generate-start">
        <div>
          <span>Start</span>

          <strong>
            {formatCoordinate(start.latitude, 'N', 'S')},{' '}
            {formatCoordinate(start.longitude, 'E', 'W')}
          </strong>
        </div>

        <button
          type="button"
          className={pickingStart ? 'pick-start active' : 'pick-start'}
          onClick={onTogglePickStart}
          disabled={busy}
        >
          {pickingStart ? 'Click the map…' : 'Pick on map'}
        </button>
      </div>

      <div className="generate-fields">
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

        <label>
          Start date
          <input
            type="date"
            value={startDate}
            onChange={(event) => setStartDate(event.target.value)}
            disabled={busy}
          />
        </label>

        <label>
          Storms
          <input
            type="number"
            min={1}
            max={10}
            value={count}
            onChange={(event) => setCount(Number(event.target.value))}
            disabled={busy}
          />
        </label>

        <label>
          Seed
          <input
            type="number"
            min={0}
            value={seed}
            onChange={(event) => setSeed(Number(event.target.value))}
            disabled={busy}
          />
        </label>
      </div>

      <button
        type="button"
        className="generate-button"
        onClick={() => onGenerate({ maxWindKt, startDate, count, seed })}
        disabled={busy}
      >
        {generating ? 'Generating…' : 'Generate'}
      </button>

      {generating && (
        <p className="generate-note">
          The first run loads the simulator, which takes a few seconds.
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
            {result.storms.length}{' '}
            {result.storms.length === 1 ? 'storm' : 'storms'} generated
          </strong>

          <span>
            {landfalls} make landfall · choose one under Storm Scenario, then
            Simulate
          </span>

          <p>{result.completeness_warning}</p>
        </div>
      )}
    </section>
  )
}

export default GenerateStorms
