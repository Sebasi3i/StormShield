import { useEffect, useState } from 'react'
import PropertyMap from './components/PropertyMap'
import Portfolio from './components/Portfolio'
import StormImpact from './components/StormImpact'
import FullAnalysis from './components/FullAnalysis'

import { properties } from './data/properties'

import type { Property } from './types/Property'
import type { Storm, StormCatalog } from './types/Storm'
import type { StormLossResponse } from './types/StormLoss'

import {
  findStorm,
  getStormCatalog,
} from './api/storms'

import { getStormLosses } from './api/stormLosses'
import { getStormColor } from './utils/stormColors'

import './App.css'

function App() {
  const [selectedProperties, setSelectedProperties] = useState<Property[]>([])

  const [stormCatalog, setStormCatalog] =
    useState<StormCatalog | null>(null)

  const [catalogLoading, setCatalogLoading] = useState(true)
  const [catalogError, setCatalogError] = useState<string | null>(null)

  const [selectedStormId, setSelectedStormId] = useState('')
  const [activeStorm, setActiveStorm] = useState<Storm | null>(null)

  const [stormLoading, setStormLoading] = useState(false)
  const [stormStep, setStormStep] = useState(0)
  const [stormAnimating, setStormAnimating] = useState(false)

  const [stormLosses, setStormLosses] =
    useState<StormLossResponse | null>(null)

  const [analysisOpen, setAnalysisOpen] = useState(false)

  /*
   * Load the complete storm catalog once when StormShield starts.
   * The UI therefore works with however many storms the backend returns.
   */
  useEffect(() => {
    const loadCatalog = async () => {
      try {
        setCatalogLoading(true)
        setCatalogError(null)

        const catalog = await getStormCatalog()

        setStormCatalog(catalog)

        if (catalog.storms.length > 0) {
          setSelectedStormId(catalog.storms[0].storm_id)
        }
      } catch (error) {
        console.error(
          'Unable to load storm catalog:',
          error,
        )

        setCatalogError(
          'Unable to load storm scenarios.',
        )
      } finally {
        setCatalogLoading(false)
      }
    }

    loadCatalog()
  }, [])

  const toggleProperty = (property: Property) => {
    setSelectedProperties((currentProperties) => {
      const alreadySelected = currentProperties.some(
        (selectedProperty) =>
          selectedProperty.id === property.id,
      )

      if (alreadySelected) {
        return currentProperties.filter(
          (selectedProperty) =>
            selectedProperty.id !== property.id,
        )
      }

      return [...currentProperties, property]
    })
  }

  const simulateCatastrophe = async () => {
    if (!stormCatalog || !selectedStormId) {
      return
    }

    try {
      setStormLoading(true)
      setStormStep(0)
      setStormLosses(null)
      setAnalysisOpen(false)

      const storm = findStorm(
        stormCatalog,
        selectedStormId,
      )

      setActiveStorm(storm)

      if (selectedProperties.length > 0) {
        const losses = await getStormLosses(
          selectedProperties,
          selectedStormId,
        )

        setStormLosses(losses)

        console.log(
          'Storm loss results:',
          losses,
        )
      }

      setStormAnimating(true)
    } catch (error) {
      console.error(
        'Unable to simulate catastrophe:',
        error,
      )
    } finally {
      setStormLoading(false)
    }
  }

  useEffect(() => {
    if (!activeStorm || !stormAnimating) {
      return
    }

    if (
      stormStep >=
      activeStorm.track.length - 1
    ) {
      setStormAnimating(false)
      return
    }

    const timer = window.setTimeout(() => {
      setStormStep(
        (currentStep) => currentStep + 1,
      )
    }, 650)

    return () =>
      window.clearTimeout(timer)
  }, [
    activeStorm,
    stormAnimating,
    stormStep,
  ])

  const currentStormPoint =
    activeStorm && activeStorm.track.length > 0
      ? activeStorm.track[
          Math.min(
            stormStep,
            activeStorm.track.length - 1,
          )
        ]
      : null

  const selectedStormIndex =
    stormCatalog?.storms.findIndex(
      (storm) =>
        storm.storm_id === selectedStormId,
    ) ?? 0

  const activeStormColor =
    getStormColor(
      selectedStormIndex >= 0
        ? selectedStormIndex
        : 0,
    )

  return (
    <main className="app">
      <header className="app-header">
        <div className="brand">
          <div className="brand-mark">
            S
          </div>

          <div>
            <h1>
              <span className="storm-word">
                Storm
              </span>

              <span className="shield-word">
                Shield
              </span>
            </h1>

            <p>
              Property Risk Intelligence
            </p>
          </div>
        </div>

        <div className="property-count">
          {selectedProperties.length}{' '}
          selected
        </div>
      </header>

      <section className="workspace">
        <div className="map-container">
          <div className="storm-controls">
            <label htmlFor="storm-select">
              Storm Scenario
            </label>

            <select
              id="storm-select"
              value={selectedStormId}
              onChange={(event) => {
                setSelectedStormId(
                  event.target.value,
                )

                setActiveStorm(null)
                setStormLosses(null)
                setAnalysisOpen(false)
                setStormStep(0)
                setStormAnimating(false)
              }}
              disabled={
                stormAnimating ||
                stormLoading ||
                catalogLoading ||
                !stormCatalog
              }
            >
              {catalogLoading && (
                <option value="">
                  Loading scenarios...
                </option>
              )}

              {!catalogLoading &&
                stormCatalog?.storms.map(
                  (storm, index) => (
                    <option
                      key={storm.storm_id}
                      value={storm.storm_id}
                    >
                      {index + 1}.{' '}
                      {storm.storm_id}
                    </option>
                  ),
                )}
            </select>

            {!catalogLoading &&
              stormCatalog && (
                <span
                  className="storm-color-indicator"
                  style={{
                    backgroundColor:
                      activeStormColor,
                  }}
                  title="Storm track color"
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
              catalogLoading ||
              !selectedStormId
            }
          >
            <span className="simulate-icon">
              ◉
            </span>

            {stormLoading
              ? 'Loading Storm...'
              : stormAnimating
                ? 'Simulating...'
                : activeStorm
                  ? 'Replay Catastrophe'
                  : 'Simulate Catastrophe'}
          </button>

          {catalogError && (
            <div className="catalog-error">
              {catalogError}
            </div>
          )}

          {activeStorm &&
            currentStormPoint && (
              <div className="storm-status">
                <div className="storm-status-header">
                  <div>
                    <span className="storm-status-label">
                      ACTIVE SCENARIO
                    </span>

                    <h2>
                      {activeStorm.storm_id}
                    </h2>
                  </div>

                  <div
                    className="storm-category"
                    style={{
                      backgroundColor:
                        activeStormColor,
                    }}
                  >
                    {currentStormPoint.category}
                  </div>
                </div>

                <div className="storm-status-time">
                  {new Date(
                    currentStormPoint.timestamp,
                  ).toLocaleString([], {
                    month: 'short',
                    day: 'numeric',
                    hour: 'numeric',
                    minute: '2-digit',
                  })}
                </div>

                <div className="storm-metric">
                  <span className="storm-metric-value">
                    {currentStormPoint.max_wind_kt.toFixed(
                      1,
                    )}
                  </span>

                  <div>
                    <span className="storm-metric-unit">
                      kt
                    </span>

                    <p>
                      Storm-center wind
                    </p>
                  </div>
                </div>

                <div className="storm-status-footer">
                  <span>
                    {currentStormPoint.is_over_land
                      ? 'Over land'
                      : 'Over water'}
                  </span>

                  <span>
                    Step {stormStep + 1} of{' '}
                    {
                      activeStorm.track
                        .length
                    }
                  </span>
                </div>

                <div className="storm-progress">
                  <div
                    className="storm-progress-bar"
                    style={{
                      width: `${
                        ((stormStep + 1) /
                          activeStorm.track
                            .length) *
                        100
                      }%`,
                      backgroundColor:
                        activeStormColor,
                    }}
                  />
                </div>
              </div>
            )}

          <PropertyMap
            properties={properties}
            selectedProperties={selectedProperties}
            onToggleProperty={toggleProperty}
            storms={stormCatalog?.storms ?? []}
            storm={activeStorm}
            stormStep={stormStep}
            stormColor={activeStormColor}
          />
        </div>

        <div className="sidebar">
          <Portfolio
            properties={
              selectedProperties
            }
            onRemoveProperty={
              toggleProperty
            }
          />

          {!stormAnimating &&
            stormLosses &&
            selectedProperties.length >
              0 && (
              <StormImpact
                properties={
                  selectedProperties
                }
                losses={stormLosses}
                onViewAnalysis={() =>
                  setAnalysisOpen(true)
                }
              />
            )}
        </div>
      </section>

      {analysisOpen &&
        stormLosses && (
          <FullAnalysis
            properties={
              selectedProperties
            }
            losses={stormLosses}
            onClose={() =>
              setAnalysisOpen(false)
            }
          />
        )}
    </main>
  )
}

export default App