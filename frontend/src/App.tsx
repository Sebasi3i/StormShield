import { useState } from 'react'
import PropertyMap from './components/PropertyMap'
import { properties } from './data/properties'
import type { Property } from './types/Property'
import Portfolio from './components/Portfolio'
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
  <div className="brand">
    <div className="brand-mark">S</div>

    <div>
      <h1>StormShield</h1>
      <p>Property Risk Intelligence</p>
    </div>
  </div>

  <div className="property-count">
    {selectedProperties.length} selected
  </div>
</header>

      <section className="workspace">
  <div className="map-container">
    <PropertyMap
      properties={properties}
      selectedProperties={selectedProperties}
      onToggleProperty={toggleProperty}
    />
  </div>

  <Portfolio
    properties={selectedProperties}
    onRemoveProperty={toggleProperty}
  />
</section>
    </main>
  )
}

export default App