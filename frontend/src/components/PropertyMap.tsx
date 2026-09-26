import {
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  TileLayer,
  useMap,
} from 'react-leaflet'

import { useEffect } from 'react'
import type { LatLngBoundsExpression } from 'leaflet'
import type { Property } from '../types/Property'
import type { Storm } from '../types/Storm'
import { getStormColor } from '../utils/stormColors'
import 'leaflet/dist/leaflet.css'

interface PropertyMapProps {
  properties: Property[]
  selectedProperties: Property[]
  onToggleProperty: (property: Property) => void
  storms: Storm[]
  storm: Storm | null
  stormStep: number
  stormColor: string
}

const MAP_BOUNDS: LatLngBoundsExpression = [
  [20.0, -91.0],
  [35.5, -69.0],
]

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
      window.removeEventListener(
        'resize',
        handleResize,
      )

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
  storm,
  stormStep,
  stormColor,
}: PropertyMapProps) {
  const currentStormPoint =
    storm && storm.track.length > 0
      ? storm.track[
          Math.min(
            stormStep,
            storm.track.length - 1,
          )
        ]
      : null

  return (
    <MapContainer
      center={[27.5, -80.5]}
      zoom={6}
      minZoom={5}
      maxZoom={12}
      maxBounds={MAP_BOUNDS}
      maxBoundsViscosity={1.0}
      worldCopyJump={false}
      className="property-map"
    >
      <TileLayer
        attribution="&copy; OpenStreetMap contributors"
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        noWrap={true}
        minZoom={5}
        maxZoom={19}
      />

      <MapResizeHandler />

      {/* Available storm scenarios */}
      {storms.map((catalogStorm, index) => {
        const isActive =
          storm?.storm_id === catalogStorm.storm_id

        /*
         * The active storm gets its own animated track below.
         * We only draw the inactive scenarios here.
         */
        if (isActive) {
          return null
        }

        return (
          <Polyline
            key={catalogStorm.storm_id}
            positions={catalogStorm.track.map(
              (point) => [
                point.latitude,
                point.longitude,
              ],
            )}
            pathOptions={{
              color: getStormColor(index),
              weight: 2,
              opacity: 0.28,
              dashArray: '5 7',
            }}
          >
            <Popup>
              <div>
                <strong>
                  {catalogStorm.storm_id}
                </strong>

                <p>
                  Peak center wind:{' '}
                  {catalogStorm.peak_wind_kt.toFixed(1)} kt
                </p>

                <p>
                  {catalogStorm.landfall
                    ? 'Florida landfall scenario'
                    : 'No modeled landfall'}
                </p>
              </div>
            </Popup>
          </Polyline>
        )
      })}

      {/* Active animated storm */}
      {storm && currentStormPoint && (
        <>
          <Polyline
            positions={storm.track
              .slice(
                0,
                Math.min(
                  stormStep + 1,
                  storm.track.length,
                ),
              )
              .map((point) => [
                point.latitude,
                point.longitude,
              ])}
            pathOptions={{
              color: stormColor,
              weight: 5,
              opacity: 0.95,
            }}
          />

          <CircleMarker
            center={[
              currentStormPoint.latitude,
              currentStormPoint.longitude,
            ]}
            radius={10}
            pathOptions={{
              color: '#ffffff',
              weight: 3,
              fillColor: stormColor,
              fillOpacity: 1,
            }}
          >
            <Popup>
              <div>
                <strong>
                  {storm.storm_id}
                </strong>

                <p>
                  Category:{' '}
                  {currentStormPoint.category}
                </p>

                <p>
                  Center wind:{' '}
                  {currentStormPoint.max_wind_kt.toFixed(
                    1,
                  )}{' '}
                  kt
                </p>

                <p>
                  {currentStormPoint.timestamp}
                </p>

                <p>
                  {currentStormPoint.is_over_land
                    ? 'Over land'
                    : 'Over water'}
                </p>
              </div>
            </Popup>
          </CircleMarker>
        </>
      )}

      {/* Portfolio properties */}
      {properties.map((property) => {
        const selected =
          selectedProperties.some(
            (selectedProperty) =>
              selectedProperty.id === property.id,
          )

        return (
          <CircleMarker
            key={property.id}
            center={[
              property.latitude,
              property.longitude,
            ]}
            radius={selected ? 10 : 7}
            pathOptions={{
              color: selected
                ? '#ffffff'
                : '#0b4f91',

              weight: selected ? 3 : 2,

              fillColor: selected
                ? '#22c55e'
                : '#2f8de4',

              fillOpacity: 1,
            }}
          >
            <Popup>
              <div className="property-popup">
                <strong>
                  {property.address}
                </strong>

                <p>
                  {property.city}, FL
                </p>

                <p>
                  {property.county} County
                </p>

                <p>
                  $
                  {property.value.toLocaleString()}
                </p>

                <button
                  type="button"
                  onClick={() =>
                    onToggleProperty(property)
                  }
                >
                  {selected
                    ? 'Remove from Portfolio'
                    : 'Add to Portfolio'}
                </button>
              </div>
            </Popup>
          </CircleMarker>
        )
      })}
    </MapContainer>
  )
}

export default PropertyMap