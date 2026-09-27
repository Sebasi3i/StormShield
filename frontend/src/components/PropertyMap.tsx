import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet'

import { Fragment, useEffect } from 'react'
import type { Property } from '../types/Property'
import type { Storm, StormStart } from '../types/Storm'
import 'leaflet/dist/leaflet.css'

interface PropertyMapProps {
  properties: Property[]
  selectedProperties: Property[]
  onToggleProperty: (property: Property) => void
  // Every storm being simulated, animated together by track step.
  storms: Storm[]
  stormStep: number
  focusedStormId: string | null
  onFocusStorm: (stormId: string) => void
  // Where a generated batch started, if one is on screen.
  start: StormStart | null
}

// Blue for a track like the catalog's; red for one that crosses Florida at
// Category 3 or stronger, which is what a Florida batch is generated for.
const TRACK_COLOR = '#1677ff'
const FLORIDA_HIT_COLOR = '#d92d20'

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
  storms,
  stormStep,
  focusedStormId,
  onFocusStorm,
  start,
}: PropertyMapProps) {
  // The focused track is drawn last so it sits on top of the others.
  const orderedStorms = [
    ...storms.filter((storm) => storm.storm_id !== focusedStormId),
    ...storms.filter((storm) => storm.storm_id === focusedStormId),
  ]

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

      {start && (
        <CircleMarker
          center={[start.latitude, start.longitude]}
          radius={7}
          pathOptions={{
            color: '#ffffff',
            weight: 3,
            fillColor: '#16a34a',
            fillOpacity: 1,
          }}
        >
          <Tooltip>
            Batch start · {start.max_wind_kt} kt on {start.date}
          </Tooltip>
        </CircleMarker>
      )}

      {orderedStorms.map((storm) => {
        // A track that has already ended holds its last point while the
        // longer ones finish.
        const step = Math.min(stormStep, storm.track.length - 1)
        const point = storm.track[step]
        const focused =
          storms.length === 1 || storm.storm_id === focusedStormId
        const color = storm.florida_hit ? FLORIDA_HIT_COLOR : TRACK_COLOR

        return (
          <Fragment key={storm.storm_id}>
            <Polyline
              positions={storm.track
                .slice(0, step + 1)
                .map((trackPoint) => [
                  trackPoint.latitude,
                  trackPoint.longitude,
                ])}
              pathOptions={{
                color,
                weight: focused ? 5 : 3,
                opacity: focused ? 0.95 : 0.45,
              }}
              eventHandlers={{
                click: () => onFocusStorm(storm.storm_id),
              }}
            >
              <Tooltip sticky>
                {storm.storm_id} · peak {Math.round(storm.peak_wind_kt)} kt
                {storm.florida_hit ? ' · Florida Cat 3+' : ''}
              </Tooltip>
            </Polyline>

            <CircleMarker
              center={[point.latitude, point.longitude]}
              radius={focused ? 10 : 6}
              pathOptions={{
                color: '#ffffff',
                weight: focused ? 3 : 2,
                fillColor: color,
                fillOpacity: focused ? 1 : 0.7,
              }}
              eventHandlers={{
                click: () => onFocusStorm(storm.storm_id),
              }}
            >
              <Popup>
                <div>
                  <strong>{storm.storm_id}</strong>

                  <p>Category: {point.category}</p>

                  <p>Center wind: {point.max_wind_kt.toFixed(1)} kt</p>

                  <p>{point.timestamp}</p>

                  {point.is_over_land && <p>Over land</p>}

                  {storm.florida_hit && (
                    <p>
                      Over Florida at Category 3+ (peak{' '}
                      {storm.florida_peak_wind_kt?.toFixed(1)} kt)
                    </p>
                  )}
                </div>
              </Popup>
            </CircleMarker>
          </Fragment>
        )
      })}

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
