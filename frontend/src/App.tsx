import { useState } from 'react'
import PropertyMap from './components/PropertyMap'
import { properties } from './data/properties'
import type { Property } from './types/Property'
import './App.css'

function App() {
  const [selectedProperties, setSelectedProperties] = useState<Property[]>([])

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

  return (
    <main className="app">
      <header className="app-header">
        <div>
          <h1>Florida Property Risk</h1>
          <p>Select properties to build an insurance portfolio.</p>
        </div>

        <div className="property-count">
          {selectedProperties.length} selected
        </div>
      </header>

      <section className="map-container">
        <PropertyMap
          properties={properties}
          selectedProperties={selectedProperties}
          onToggleProperty={toggleProperty}
        />
      </section>
    </main>
  )
}

export default App