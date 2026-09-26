import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
  useMapEvents,
} from 'react-leaflet'

import { useEffect } from 'react'
import type { Property } from '../types/Property'
import type { GenerationStart, Storm } from '../types/Storm'
import 'leaflet/dist/leaflet.css'

interface PropertyMapProps {
  properties: Property[]
  selectedProperties: Property[]
  onToggleProperty: (property: Property) => void
  storm: Storm | null
  stormStep: number
  generationStart: GenerationStart
  pickingStart: boolean
  onPickStart: (start: GenerationStart) => void
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
/*
 * While the generator is picking a start, the next map click sets it. Other
 * clicks leave it alone, so browsing the map never moves it by accident.
 */
function StartPicker({
  active,
  onPick,
}: {
  active: boolean
  onPick: (start: GenerationStart) => void
}) {
  const map = useMapEvents({
    click(event) {
      if (!active) {
        return
      }

      // Leaflet reports longitudes past +/-180 after panning around the globe.
      const longitude = ((((event.latlng.lng + 180) % 360) + 360) % 360) - 180

      onPick({ latitude: event.latlng.lat, longitude })
    },
  })

  useEffect(() => {
    map.getContainer().classList.toggle('picking-start', active)
  }, [map, active])

  return null
}

function PropertyMap({
  properties,
  selectedProperties,
  onToggleProperty,
  storm,
  stormStep,
  generationStart,
  pickingStart,
  onPickStart,
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
      <StartPicker active={pickingStart} onPick={onPickStart} />

      <CircleMarker
        center={[generationStart.latitude, generationStart.longitude]}
        radius={7}
        pathOptions={{
          color: '#ffffff',
          weight: 3,
          fillColor: '#16a34a',
          fillOpacity: 1,
        }}
      >
        <Tooltip>Storm generator start</Tooltip>
      </CircleMarker>
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