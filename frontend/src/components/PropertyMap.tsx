import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  useMap,
} from 'react-leaflet'

import { useEffect } from 'react'
import type { Property } from '../types/Property'
import type { Storm } from '../types/Storm'
import 'leaflet/dist/leaflet.css'

interface PropertyMapProps {
  properties: Property[]
  selectedProperties: Property[]
  onToggleProperty: (property: Property) => void
  storm: Storm | null
  stormStep: number
}
function MapResizeHandler() {
  const map = useMap()

  useEffect(() => {
    const handleResize = () => {
      map.invalidateSize()
    }

    window.addEventListener('resize', handleResize)

    const timer = window.setTimeout(() => {
      map.invalidateSize()
    }, 100)

    return () => {
      window.removeEventListener('resize', handleResize)
      window.clearTimeout(timer)
    }
  }, [map])

  return null
}
function PropertyMap({
  properties,
  selectedProperties,
  onToggleProperty,
  storm,
  stormStep,
}: PropertyMapProps) {
  return (
    <MapContainer
     center={[27.0, -78.5]}
     zoom={6}
     className="property-map"
    >
      <TileLayer
        attribution="&copy; OpenStreetMap contributors"
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <MapResizeHandler />
      {storm && (
  <>
    <Polyline
      positions={storm.track
        .slice(0, stormStep + 1)
        .map((point) => [
          point.latitude,
          point.longitude,
        ])}
      pathOptions={{
        color: '#1677ff',
        weight: 4,
        opacity: 0.9,
      }}
    />

    <CircleMarker
      center={[
        storm.track[stormStep].latitude,
        storm.track[stormStep].longitude,
      ]}
      radius={10}
      pathOptions={{
        color: '#ffffff',
        weight: 3,
        fillColor: '#1677ff',
        fillOpacity: 1,
      }}
    >
      <Popup>
        <div>
          <strong>{storm.storm_id}</strong>

          <p>
            Category: {storm.track[stormStep].category}
          </p>

          <p>
            Center wind:{' '}
            {storm.track[stormStep].max_wind_kt.toFixed(1)} kt
          </p>

          <p>{storm.track[stormStep].timestamp}</p>

          {storm.track[stormStep].is_over_land && (
            <p>Over land</p>
          )}
        </div>
      </Popup>
    </CircleMarker>
  </>
)}

      {properties.map((property) => {
        const selected = selectedProperties.some(
          (selectedProperty) => selectedProperty.id === property.id,
        )

        return (
          <Marker
            key={property.id}
            position={[property.latitude, property.longitude]}
          >
            <Popup>
              <div className="property-popup">
                <strong>{property.address}</strong>

                <p>
                  {property.city}, FL
                </p>

                <p>{property.county} County</p>

                <p>
                  ${property.value.toLocaleString()}
                </p>

                <button
                  type="button"
                  onClick={() => onToggleProperty(property)}
                >
                  {selected ? 'Remove from Portfolio' : 'Add to Portfolio'}
                </button>
              </div>
            </Popup>
          </Marker>
        )
      })}
    </MapContainer>
  )
}

export default PropertyMap