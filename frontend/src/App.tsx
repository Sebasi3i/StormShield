import { useEffect, useState } from 'react'
import PropertyMap from './components/PropertyMap'
import Portfolio from './components/Portfolio'
import { properties } from './data/properties'
import type { Property } from './types/Property'
import type {
  FloridaStormBatch,
  Storm,
  StormCatalog,
} from './types/Storm'
import { generateFloridaStorms, getStormCatalog } from './api/storms'
import type { StormLossResponse } from './types/StormLoss'
import { getStormLosses, lossesForStorm } from './api/stormLosses'
import { getStormColor } from './utils/stormColors'
import StormImpact from './components/StormImpact'
import StormBatch from './components/StormBatch'
import FullAnalysis from './components/FullAnalysis'
import BatchSettings from './components/BatchSettings'
import './App.css'

// The Storm Scenario value that means "the whole generated batch".
const FLORIDA_BATCH = 'florida-batch'
const DEFAULT_CATALOG_STORM = 'SYN0155'

const DISPLAY_REGION = {
  minLat: 18.0,
  maxLat: 36.0,
  minLon: -94.0,
  maxLon: -67.0,
}

function isInDisplayRegion(
  latitude: number,
  longitude: number,
) {
  return (
    latitude >= DISPLAY_REGION.minLat &&
    latitude <= DISPLAY_REGION.maxLat &&
    longitude >= DISPLAY_REGION.minLon &&
    longitude <= DISPLAY_REGION.maxLon
  )
}

function getLastVisibleStep(storm: Storm) {
  let enteredRegion = false
  let lastVisibleStep = 0

  for (let i = 0; i < storm.track.length; i += 1) {
    const point = storm.track[i]

    if (
      isInDisplayRegion(
        point.latitude,
        point.longitude,
      )
    ) {
      enteredRegion = true
      lastVisibleStep = i
    } else if (enteredRegion) {
      break
    }
  }

  return lastVisibleStep
}

function App() {
  const [selectedProperties, setSelectedProperties] = useState<Property[]>([])

  // The three catalog tracks, loaded from the API.
  const [catalog, setCatalog] = useState<StormCatalog | null>(null)
  const [catalogError, setCatalogError] = useState<string | null>(null)

  // Storm Scenario: a catalog storm id, or the generated Florida batch.
  const [scenario, setScenario] = useState(DEFAULT_CATALOG_STORM)

  // The storms being simulated (one catalog storm, or the whole batch),
  // animated together by track step. The focused one drives the status
  // panel and the impact cards.
  const [activeStorms, setActiveStorms] = useState<Storm[]>([])
  const [focusedStormId, setFocusedStormId] = useState<string | null>(null)
  const [stormLoading, setStormLoading] = useState(false)
  const [stormStep, setStormStep] = useState(0)
  const [stormProgress, setStormProgress] = useState(0)
  const [stormAnimating, setStormAnimating] = useState(false)
  const [stormLosses, setStormLosses] =
    useState<StormLossResponse | null>(null)
  const [stormError, setStormError] = useState<string | null>(null)
  const [analysisOpen, setAnalysisOpen] = useState(false)

  // Florida batch generator: its settings live under Storm Scenario, and
  // Simulate generates the batch (or reuses the one already generated for
  // these settings) before running it.
  const [batchSeed, setBatchSeed] = useState(42)
  const [batchWindKt, setBatchWindKt] = useState(70)
  const [batch, setBatch] = useState<FloridaStormBatch | null>(null)
  const [generating, setGenerating] = useState(false)
  const [generationError, setGenerationError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    getStormCatalog()
      .then((loaded) => {
        if (!cancelled) {
          setCatalog(loaded)
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setCatalogError(
            error instanceof Error
              ? error.message
              : 'Failed to load storm catalog',
          )
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  const toggleProperty = (property: Property) => {
    setSelectedProperties((currentProperties) => {
      const alreadySelected = currentProperties.some(
        (selectedProperty) => selectedProperty.id === property.id,
      )

      if (alreadySelected) {
        return currentProperties.filter(
          (selectedProperty) => selectedProperty.id !== property.id,
        )
      }

      return [...currentProperties, property]
    })
  }

  const resetSimulation = () => {
    setActiveStorms([])
    setFocusedStormId(null)
    setStormStep(0)
    setStormProgress(0)
    setStormAnimating(false)
    setStormLosses(null)
    setStormError(null)
    setAnalysisOpen(false)
  }

  // The batch for the current settings: the one already generated, or a new
  // search. Returns null when generation failed (the error is shown).
  const batchForSettings = async (): Promise<FloridaStormBatch | null> => {
    if (
      batch &&
      batch.generator.seed === batchSeed &&
      batch.generator.start.max_wind_kt === batchWindKt
    ) {
      return batch
    }

    try {
      setGenerating(true)
      setGenerationError(null)
      resetSimulation()

      const generated = await generateFloridaStorms({
        seed: batchSeed,
        count: 10,
        max_wind_kt: batchWindKt,
        min_florida_hits: 2,
      })

      setBatch(generated)
      return generated
    } catch (error) {
      setGenerationError(
        error instanceof Error ? error.message : 'Storm generation failed.',
      )
      return null
    } finally {
      setGenerating(false)
    }
  }

  const simulateCatastrophe = async () => {
    // Which storms this scenario runs. The batch is not in the API's catalog,
    // so it travels with the pricing request.
    let storms: Storm[]
    let supplied: Storm[] | undefined

    if (scenario === FLORIDA_BATCH) {
      const current = await batchForSettings()

      if (!current) {
        return
      }

      storms = current.storms
      supplied = current.storms
    } else {
      const catalogStorm = catalog?.storms.find(
        (storm) => storm.storm_id === scenario,
      )

      if (!catalogStorm) {
        setStormError(`Storm ${scenario} was not found in the catalog.`)
        return
      }

      storms = [catalogStorm]
    }

    try {
      setStormLoading(true)
      setStormStep(0)
      setStormProgress(0)
      setStormLosses(null)
      setStormError(null)
      setAnalysisOpen(false)
      setActiveStorms(storms)

      // Focus the first Florida hit so the batch opens on a storm that matters.
      const firstHit = storms.find((storm) => storm.florida_hit)
      setFocusedStormId((firstHit ?? storms[0]).storm_id)

      if (selectedProperties.length > 0) {
        const losses = await getStormLosses(
          selectedProperties,
          storms.map((storm) => storm.storm_id),
          supplied,
        )

        setStormLosses(losses)
      }

      // A one-point track has nothing to animate.
      setStormAnimating(
        storms.some((storm) => storm.track.length > 1),
      )
    } catch (error) {
      console.error('Unable to simulate catastrophe:', error)
      setStormError(
        error instanceof Error ? error.message : 'Unable to simulate.',
      )
    } finally {
      setStormLoading(false)
    }
  }

  // Every active track advances one step per tick; shorter tracks hold their
  // last point until the longest one finishes.
  const lastStep = Math.max(
    0,
    ...activeStorms.map((storm) => storm.track.length - 1),
  )

  useEffect(() => {
      if (
        activeStorms.length === 0 ||
        !stormAnimating ||
        stormStep >= lastStep
      ) {
        return
      }

      const trackLength = lastStep + 1

      // Keep roughly the same overall playback duration as before,
      // but render many small movements between each real track point.
      const stepDuration =
        trackLength > 40
          ? Math.max(200, 26000 / trackLength)
          : 650

      const frameDelay = 30
      const progressIncrement =
        frameDelay / stepDuration

      const timer = window.setTimeout(() => {
        const nextProgress =
          stormProgress + progressIncrement

        if (nextProgress >= 1) {
          const nextStep = stormStep + 1

          setStormStep(nextStep)
          setStormProgress(0)

          if (nextStep >= lastStep) {
            setStormAnimating(false)
          }
        } else {
          setStormProgress(nextProgress)
        }
      }, frameDelay)

      return () => window.clearTimeout(timer)
    }, [
      activeStorms,
      stormAnimating,
      stormStep,
      stormProgress,
      lastStep,
    ])

  const focusedStorm =
    activeStorms.find((storm) => storm.storm_id === focusedStormId) ??
    activeStorms[0] ??
    null

  const focusedVisibleStep = focusedStorm
  ? Math.min(
      stormStep,
      getLastVisibleStep(focusedStorm),
    )
  : 0

  const focusedPoint = focusedStorm
    ? focusedStorm.track[focusedVisibleStep]
    : null

  // Single-storm views read the focused storm's rows out of the batch run.
  const focusedLosses =
    stormLosses && focusedStorm
      ? lossesForStorm(stormLosses, focusedStorm.storm_id)
      : null

  const isBatch = activeStorms.length > 1

  // Each catalog storm keeps its own colour on the map and in the controls;
  // a batch colours by Florida hit instead (see PropertyMap).
  const catalogStorms = catalog?.storms ?? []
  const catalogColors = new Map(
    catalogStorms.map((storm, index) => [storm.storm_id, getStormColor(index)]),
  )
  const scenarioColor =
    scenario === FLORIDA_BATCH ? null : (catalogColors.get(scenario) ?? null)

  return (
    <main className="app">
      <header className="app-header">
        <div className="brand">
          <div className="brand-mark">S</div>

          <div>
            <h1>
              <span className="storm-word">Storm</span>
              <span className="shield-word">Shield</span>
            </h1>

            <p>Property Risk Intelligence</p>
          </div>
        </div>

        <div className="property-count">
          {selectedProperties.length} selected
        </div>
      </header>

      <section className="workspace">
        <div className="map-container">
        <div className="storm-controls">
          <div className="storm-controls-row">
            <label htmlFor="storm-select">Storm Scenario</label>

            <select
            id="storm-select"
            value={scenario}
            onChange={(event) => {
              setScenario(event.target.value)
              resetSimulation()
            }}
            disabled={stormAnimating || stormLoading || generating}
          >
            <optgroup label="Catalog">
              {(catalog?.storms ?? []).map((storm) => (
                <option key={storm.storm_id} value={storm.storm_id}>
                  {storm.storm_id} · {Math.round(storm.peak_wind_kt)} kt
                  {storm.landfall ? ' · landfall' : ''}
                </option>
              ))}

              {!catalog && (
                <option value={DEFAULT_CATALOG_STORM}>
                  {catalogError ? 'Catalog unavailable' : 'Loading catalog…'}
                </option>
              )}
            </optgroup>

            <optgroup label="Generator">
              <option value={FLORIDA_BATCH}>
                Generate 10 Florida storms
              </option>
            </optgroup>
          </select>

            {scenarioColor && (
              <span
                className="storm-color-indicator"
                style={{ backgroundColor: scenarioColor }}
                title="Storm track colour"
              />
            )}
          </div>

          {scenario === FLORIDA_BATCH && (
            <BatchSettings
              seed={batchSeed}
              maxWindKt={batchWindKt}
              onSeedChange={(seed) => {
                setBatchSeed(seed)
                resetSimulation()
              }}
              onMaxWindKtChange={(maxWindKt) => {
                setBatchWindKt(maxWindKt)
                resetSimulation()
              }}
              disabled={generating || stormLoading || stormAnimating}
              generating={generating}
              error={generationError}
              batch={batch}
            />
          )}
        </div>
          <button
            className="simulate-button"
            type="button"
            onClick={simulateCatastrophe}
            disabled={
              stormLoading ||
              stormAnimating ||
              generating ||
              (scenario !== FLORIDA_BATCH && !catalog)
            }
          >
            <span className="simulate-icon">◉</span>

            {generating
              ? 'Generating Storms...'
              : stormLoading
                ? 'Loading Storm...'
                : stormAnimating
                  ? 'Simulating...'
                  : activeStorms.length > 0
                    ? 'Replay Catastrophe'
                    : scenario === FLORIDA_BATCH
                      ? 'Generate & Simulate'
                      : 'Simulate Catastrophe'}
          </button>

          {(stormError || catalogError) && (
            <div className="storm-error" role="alert">
              {stormError ?? catalogError}
            </div>
          )}

          {focusedStorm && focusedPoint && (
            <div className="storm-status">
              <div className="storm-status-header">
                <div>
                  <span className="storm-status-label">
                    {isBatch ? 'FOCUSED STORM' : 'ACTIVE SCENARIO'}
                  </span>

                  <h2>{focusedStorm.storm_id}</h2>
                </div>

                <div
                  className="storm-category"
                  style={
                    !isBatch && scenarioColor
                      ? { backgroundColor: scenarioColor }
                      : undefined
                  }
                >
                  {focusedPoint.category}
                </div>
              </div>

              {isBatch && (
                <div
                  className={
                    focusedStorm.florida_hit
                      ? 'storm-status-batch hit'
                      : 'storm-status-batch'
                  }
                >
                  {focusedStorm.florida_hit
                    ? 'Crosses Florida at Category 3+'
                    : 'Misses Florida at Category 3+'}
                  {' · '}
                  {activeStorms.findIndex(
                    (storm) => storm.storm_id === focusedStorm.storm_id,
                  ) + 1}{' '}
                  of {activeStorms.length}
                </div>
              )}

              <div className="storm-status-time">
                {new Date(focusedPoint.timestamp).toLocaleString([], {
                  month: 'short',
                  day: 'numeric',
                  hour: 'numeric',
                  minute: '2-digit',
                })}
              </div>

              <div className="storm-metric">
                <span className="storm-metric-value">
                  {focusedPoint.max_wind_kt.toFixed(1)}
                </span>

                <div>
                  <span className="storm-metric-unit">kt</span>
                  <p>Storm maximum sustained wind</p>
                </div>
              </div>

              <div className="storm-status-footer">
                <span>
                  {focusedPoint.is_over_land
                    ? 'Over land'
                    : 'Over water'}
                </span>

                <span>
                  Step {focusedVisibleStep + 1}{' '}
                  of {getLastVisibleStep(focusedStorm) + 1}
                </span>
              </div>

              <div className="storm-progress">
                <div
                  className="storm-progress-bar"
                  style={{
                    width: `${
                      ((focusedVisibleStep + 1) /
                        (getLastVisibleStep(focusedStorm) + 1)) *
                      100
                    }%`,
                    ...(!isBatch && scenarioColor
                      ? { backgroundColor: scenarioColor }
                      : {}),
                  }}
                />
              </div>
            </div>
          )}

          <PropertyMap
            properties={properties}
            selectedProperties={selectedProperties}
            onToggleProperty={toggleProperty}
            storms={activeStorms}
            stormStep={stormStep}
            stormProgress={stormProgress}
            focusedStormId={focusedStorm?.storm_id ?? null}
            onFocusStorm={setFocusedStormId}
            start={isBatch && batch ? batch.generator.start : null}
            catalogStorms={isBatch ? [] : catalogStorms}
            catalogColors={catalogColors}
          />
        </div>

        <div className="sidebar">
          <Portfolio
            properties={selectedProperties}
            onRemoveProperty={toggleProperty}
            onAnalyzePortfolio={() => {
              if (stormLosses) {
                setAnalysisOpen(true)
              }
            }}
            analysisAvailable={stormLosses !== null}
          />

          {isBatch && (
            <StormBatch
              batch={batch}
              storms={activeStorms}
              focusedStormId={focusedStorm?.storm_id ?? null}
              onFocusStorm={setFocusedStormId}
              losses={stormAnimating ? null : stormLosses}
            />
          )}

          {!stormAnimating &&
            focusedLosses &&
            selectedProperties.length > 0 && (
              <StormImpact
                properties={selectedProperties}
                losses={focusedLosses}
              />
            )}

        </div>
      </section>
      {analysisOpen && stormLosses && (
        <FullAnalysis
          properties={selectedProperties}
          losses={stormLosses}
          onClose={() => setAnalysisOpen(false)}
        />
      )}
    </main>
  )
}

export default App
