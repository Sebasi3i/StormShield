import { useEffect, useState } from 'react'
import PropertyMap from './components/PropertyMap'
import Portfolio from './components/Portfolio'
import { properties } from './data/properties'
import type { Property } from './types/Property'
import type { Storm } from './types/Storm'
import { getStorm } from './api/storms'
import type { StormLossResponse } from './types/StormLoss'
import { getStormLosses } from './api/stormLosses'
import StormImpact from './components/StormImpact'
import FullAnalysis from './components/FullAnalysis'
import './App.css'

function App() {
  const [selectedProperties, setSelectedProperties] = useState<Property[]>([])
  const [activeStorm, setActiveStorm] = useState<Storm | null>(null)
  const [stormLoading, setStormLoading] = useState(false)
  const [stormStep, setStormStep] = useState(0)
  const [stormAnimating, setStormAnimating] = useState(false)
  const [selectedStormId, setSelectedStormId] = useState('SYN0155')
  const [stormLosses, setStormLosses] =
  useState<StormLossResponse | null>(null)
  const [analysisOpen, setAnalysisOpen] = useState(false)

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

  const simulateCatastrophe = async () => {
    try {
      setStormLoading(true)
      setStormStep(0)
      setStormLosses(null)
      setAnalysisOpen(false)

      const storm = await getStorm(selectedStormId)

      setActiveStorm(storm)

      if (selectedProperties.length > 0) {
        const losses = await getStormLosses(
          selectedProperties,
          selectedStormId,
        )

        setStormLosses(losses)

        console.log('Storm loss results:', losses)
      }

      setStormAnimating(true)
    } catch (error) {
      console.error('Unable to simulate catastrophe:', error)
    } finally {
      setStormLoading(false)
    }
  }

  useEffect(() => {
    if (!activeStorm || !stormAnimating) {
      return
    }

    if (stormStep >= activeStorm.track.length - 1) {
      setStormAnimating(false)
      return
    }

    const timer = window.setTimeout(() => {
      setStormStep((currentStep) => currentStep + 1)
    }, 650)

    return () => window.clearTimeout(timer)
  }, [activeStorm, stormAnimating, stormStep])

  const currentStormPoint = activeStorm
    ? activeStorm.track[stormStep]
    : null

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
        <div className="storm-controls">
  <label htmlFor="storm-select">Storm Scenario</label>

  <select
    id="storm-select"
    value={selectedStormId}
    onChange={(event) => {
      setSelectedStormId(event.target.value)
      setActiveStorm(null)
      setStormStep(0)
      setStormAnimating(false)
    }}
    disabled={stormAnimating || stormLoading}
  >
    <option value="SYN0155">SYN0155</option>
    <option value="SYN0697">SYN0697</option>
    <option value="SYN0973">SYN0973</option>
  </select>
</div>
        <div className="map-container">
          <button
            className="simulate-button"
            type="button"
            onClick={simulateCatastrophe}
            disabled={stormLoading || stormAnimating}
          >
            <span className="simulate-icon">◉</span>

            {stormLoading
              ? 'Loading Storm...'
              : stormAnimating
                ? 'Simulating...'
                : activeStorm
                  ? 'Replay Catastrophe'
                  : 'Simulate Catastrophe'}
          </button>

          {activeStorm && currentStormPoint && (
            <div className="storm-status">
              <div className="storm-status-header">
                <div>
                  <span className="storm-status-label">
                    ACTIVE SCENARIO
                  </span>

                  <h2>{activeStorm.storm_id}</h2>
                </div>

                <div className="storm-category">
                  {currentStormPoint.category}
                </div>
              </div>

              <div className="storm-status-time">
                {new Date(currentStormPoint.timestamp).toLocaleString([], {
                  month: 'short',
                  day: 'numeric',
                  hour: 'numeric',
                  minute: '2-digit',
                })}
              </div>

              <div className="storm-metric">
                <span className="storm-metric-value">
                  {currentStormPoint.max_wind_kt.toFixed(1)}
                </span>

                <div>
                  <span className="storm-metric-unit">kt</span>
                  <p>Storm-center wind</p>
                </div>
              </div>

              <div className="storm-status-footer">
                <span>
                  {currentStormPoint.is_over_land
                    ? 'Over land'
                    : 'Over water'}
                </span>

                <span>
                  Step {stormStep + 1} of {activeStorm.track.length}
                </span>
              </div>

              <div className="storm-progress">
                <div
                  className="storm-progress-bar"
                  style={{
                    width: `${
                      ((stormStep + 1) / activeStorm.track.length) * 100
                    }%`,
                  }}
                />
              </div>
            </div>
          )}

          <PropertyMap
            properties={properties}
            selectedProperties={selectedProperties}
            onToggleProperty={toggleProperty}
            storm={activeStorm}
            stormStep={stormStep}
          />
        </div>

        <div className="sidebar">
        <Portfolio
          properties={selectedProperties}
          onRemoveProperty={toggleProperty}
        />

        {!stormAnimating &&
        stormLosses &&
        selectedProperties.length > 0 && (
          <StormImpact
            properties={selectedProperties}
            losses={stormLosses}
            onViewAnalysis={() => setAnalysisOpen(true)}
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