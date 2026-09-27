import {
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet'

import { Fragment, useEffect } from 'react'
import { LatLngBounds } from 'leaflet'
import type { LatLngBoundsExpression } from 'leaflet'
import type { Property } from '../types/Property'
import type {
  Storm,
  StormStart,
  StormTrackPoint,
} from '../types/Storm'
import { getStormColor } from '../utils/stormColors'
import { buildLabel } from '../utils/propertyLabels'
import 'leaflet/dist/leaflet.css'

interface PropertyMapProps {
  properties: Property[]
  selectedProperties: Property[]
  onToggleProperty: (property: Property) => void
  storms: Storm[]
  stormStep: number
  stormProgress: number
  focusedStormId: string | null
  onFocusStorm: (stormId: string) => void
  start: StormStart | null
  catalogStorms: Storm[]
  catalogColors: Map<string, string>
}

/*
 * Keep the StormShield visualization centered on Florida and
 * the nearby hurricane approach region.
 *
 * The backend still retains the complete Atlantic storm track.
 * We only limit what is displayed on this Florida risk map.
 */
const MAP_BOUNDS: LatLngBoundsExpression = [
  [18.0, -94.0],
  [36.0, -67.0],
]

const DISPLAY_REGION = {
  minLat: 18.0,
  maxLat: 36.0,
  minLon: -94.0,
  maxLon: -67.0,
}

/*
 * Returns true when a modeled storm point falls inside the
 * portion of the Atlantic displayed by StormShield.
 */
function isInDisplayRegion(
  point: StormTrackPoint,
): boolean {
  return (
    point.latitude >= DISPLAY_REGION.minLat &&
    point.latitude <= DISPLAY_REGION.maxLat &&
    point.longitude >= DISPLAY_REGION.minLon &&
    point.longitude <= DISPLAY_REGION.maxLon
  )
}

/*
 * Find the last consecutive track point that should be shown
 * on the Florida-focused map.
 *
 * A storm may begin outside the display region and later enter
 * it. Once it has entered and then leaves, its displayed
 * animation stops at the last visible point.
 */
function getLastVisibleStep(
  storm: Storm,
): number {
  let enteredRegion = false
  let lastVisibleStep = 0

  for (
    let index = 0;
    index < storm.track.length;
    index += 1
  ) {
    const point = storm.track[index]

    if (isInDisplayRegion(point)) {
      enteredRegion = true
      lastVisibleStep = index
    } else if (enteredRegion) {
      break
    }
  }

  return lastVisibleStep
}

/*
 * Return only the first continuous visible portion of a storm
 * track.
 *
 * This prevents Leaflet from connecting two separated visible
 * portions of a track with an artificial straight line.
 */
function getVisibleTrack(
  storm: Storm,
): StormTrackPoint[] {
  const visibleTrack: StormTrackPoint[] = []
  let enteredRegion = false

  for (const point of storm.track) {
    if (isInDisplayRegion(point)) {
      enteredRegion = true
      visibleTrack.push(point)
    } else if (enteredRegion) {
      break
    }
  }

  return visibleTrack
}

function MapResizeHandler() {
  const map = useMap()

  useEffect(() => {
    const handleResize = () => {
      map.invalidateSize()
    }

    window.addEventListener(
      'resize',
      handleResize,
    )

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

/*
 * Fit generated storms to the Florida-relevant portion of
 * their tracks rather than the complete Atlantic basin.
 */
function FitToStorms({
  storms,
}: {
  storms: Storm[]
}) {
  const map = useMap()

  const batchKey =
    storms.length > 1
      ? storms
          .map((storm) => storm.storm_id)
          .join(',')
      : ''

  useEffect(() => {
    if (!batchKey) {
      return
    }

    const bounds = new LatLngBounds([])

    storms.forEach((storm) => {
      getVisibleTrack(storm).forEach(
        (point) => {
          bounds.extend([
            point.latitude,
            point.longitude,
          ])
        },
      )
    })

    /*
     * Always keep Florida itself in frame.
     */
    bounds.extend([31.0, -87.6])
    bounds.extend([24.5, -80.0])

    if (bounds.isValid()) {
      map.fitBounds(bounds, {
        padding: [45, 45],
        maxZoom: 6,
      })
    }
  }, [map, batchKey, storms])

  return null
}

function PropertyMap({
  properties,
  selectedProperties,
  onToggleProperty,
  storms,
  stormStep,
  stormProgress,
  focusedStormId,
  onFocusStorm,
  start,
  catalogStorms,
  catalogColors,
}: PropertyMapProps) {
  /*
   * Render the focused storm last so its thicker line and
   * marker stay visually above the other storms.
   */
  const orderedStorms = [
    ...storms.filter(
      (storm) =>
        storm.storm_id !== focusedStormId,
    ),
    ...storms.filter(
      (storm) =>
        storm.storm_id === focusedStormId,
    ),
  ]

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

      <FitToStorms storms={storms} />

      {/* Generated batch starting location */}
      {start &&
        start.latitude >=
          DISPLAY_REGION.minLat &&
        start.latitude <=
          DISPLAY_REGION.maxLat &&
        start.longitude >=
          DISPLAY_REGION.minLon &&
        start.longitude <=
          DISPLAY_REGION.maxLon && (
          <CircleMarker
            center={[
              start.latitude,
              start.longitude,
            ]}
            radius={7}
            pathOptions={{
              color: '#ffffff',
              weight: 3,
              fillColor: '#16a34a',
              fillOpacity: 1,
            }}
          >
            <Tooltip>
              Batch start · {start.max_wind_kt}{' '}
              kt on {start.date}
            </Tooltip>
          </CircleMarker>
        )}

      {/* Existing catalog scenarios */}
      {catalogStorms.map(
        (catalogStorm, index) => {
          const running = storms.some(
            (storm) =>
              storm.storm_id ===
              catalogStorm.storm_id,
          )

          if (running) {
            return null
          }

          const visibleTrack =
            getVisibleTrack(catalogStorm)

          if (visibleTrack.length < 2) {
            return null
          }

          return (
            <Polyline
              key={`catalog-${catalogStorm.storm_id}`}
              positions={visibleTrack.map(
                (point) => [
                  point.latitude,
                  point.longitude,
                ],
              )}
              pathOptions={{
                color:
                  catalogColors.get(
                    catalogStorm.storm_id,
                  ) ??
                  getStormColor(index),
                weight: 2,
                opacity: 0.25,
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
                    {catalogStorm.peak_wind_kt.toFixed(
                      1,
                    )}{' '}
                    kt
                  </p>

                  <p>
                    {catalogStorm.landfall
                      ? 'Landfall scenario'
                      : 'No modeled landfall'}
                  </p>
                </div>
              </Popup>
            </Polyline>
          )
        },
      )}

      {/* Generated / actively simulated storms */}
      {orderedStorms.map((storm) => {
        const stormIndex =
          storms.findIndex(
            (candidate) =>
              candidate.storm_id ===
              storm.storm_id,
          )

        const color = getStormColor(
          Math.max(stormIndex, 0),
        )

        const focused =
          storms.length === 1 ||
          storm.storm_id ===
            focusedStormId

        /*
         * Each storm has its own visible endpoint.
         *
         * The batch can continue animating globally while a
         * storm that has already left the display region
         * remains frozen at its final visible point.
         */
        const lastVisibleStep =
          getLastVisibleStep(storm)

        const visibleStormStep = Math.min(
          stormStep,
          lastVisibleStep,
        )

        const visibleStormProgress =
          stormStep >= lastVisibleStep
            ? 0
            : stormProgress

        /*
         * Build the continuous visible portion of the track
         * up to this storm's current displayed step.
         */
        const trackUpToCurrentStep =
          storm.track.slice(
            0,
            Math.min(
              visibleStormStep + 1,
              storm.track.length,
            ),
          )

        const animatedTrack: StormTrackPoint[] =
          []

        for (
          const trackPoint of
          trackUpToCurrentStep
        ) {
          if (
            isInDisplayRegion(trackPoint)
          ) {
            animatedTrack.push(trackPoint)
          } else if (
            animatedTrack.length > 0
          ) {
            /*
             * Once the storm has entered the region and then
             * leaves it, stop drawing its visible track.
             */
            break
          }
        }

        /*
         * A storm that has not entered our display region yet
         * should not have a marker or active line.
         */
        if (animatedTrack.length === 0) {
          return null
        }

        const currentPoint =
          storm.track[
            Math.min(
              visibleStormStep,
              storm.track.length - 1,
            )
          ]

        const nextPoint =
          storm.track[
            Math.min(
              visibleStormStep + 1,
              lastVisibleStep,
            )
          ]

        /*
         * Smoothly interpolate the marker between the real
         * modeled track observations.
         *
         * This affects visualization only. The underlying
         * storm observations remain unchanged.
         */
        const interpolatedPoint = {
          ...currentPoint,

          latitude:
            currentPoint.latitude +
            (nextPoint.latitude -
              currentPoint.latitude) *
              visibleStormProgress,

          longitude:
            currentPoint.longitude +
            (nextPoint.longitude -
              currentPoint.longitude) *
              visibleStormProgress,
        }

        const interpolatedPointVisible =
          isInDisplayRegion(
            interpolatedPoint,
          )

        /*
         * Once interpolation would move beyond the display
         * region, freeze the marker at the last valid point.
         */
        const point =
          interpolatedPointVisible
            ? interpolatedPoint
            : currentPoint

        /*
         * Only append the interpolated point while it remains
         * inside the display region. This prevents long
         * artificial lines after a storm exits the map.
         */
        const animatedPositions: [
          number,
          number,
        ][] = animatedTrack.map(
          (trackPoint) => [
            trackPoint.latitude,
            trackPoint.longitude,
          ],
        )

        if (
          interpolatedPointVisible &&
          visibleStormProgress > 0
        ) {
          animatedPositions.push([
            interpolatedPoint.latitude,
            interpolatedPoint.longitude,
          ])
        }

        return (
          <Fragment key={storm.storm_id}>
            {animatedPositions.length >=
              2 && (
              <Polyline
                positions={
                  animatedPositions
                }
                pathOptions={{
                  color,
                  weight: focused
                    ? 5
                    : 3,
                  opacity: focused
                    ? 0.95
                    : 0.38,
                }}
                eventHandlers={{
                  click: () =>
                    onFocusStorm(
                      storm.storm_id,
                    ),
                }}
              >
                <Tooltip sticky>
                  {storm.storm_id} · peak{' '}
                  {Math.round(
                    storm.peak_wind_kt,
                  )}{' '}
                  kt
                  {storm.florida_hit
                    ? ' · Florida Cat 3+'
                    : ''}
                </Tooltip>
              </Polyline>
            )}

            <CircleMarker
              center={[
                point.latitude,
                point.longitude,
              ]}
              radius={focused ? 10 : 6}
              pathOptions={{
                color: '#ffffff',
                weight: focused ? 3 : 2,
                fillColor: color,
                fillOpacity: focused
                  ? 1
                  : 0.7,
              }}
              eventHandlers={{
                click: () =>
                  onFocusStorm(
                    storm.storm_id,
                  ),
              }}
            >
              <Popup>
                <div>
                  <strong>
                    {storm.storm_id}
                  </strong>

                  <p>
                    Category:{' '}
                    {point.category}
                  </p>

                  <p>
                    Center wind:{' '}
                    {point.max_wind_kt.toFixed(
                      1,
                    )}{' '}
                    kt
                  </p>

                  <p>
                    {point.timestamp}
                  </p>

                  {point.is_over_land && (
                    <p>Over land</p>
                  )}

                  {storm.florida_hit && (
                    <p>
                      Crosses Florida at
                      Category 3+ (peak{' '}
                      {storm.florida_peak_wind_kt?.toFixed(
                        1,
                      )}{' '}
                      kt)
                    </p>
                  )}
                </div>
              </Popup>
            </CircleMarker>
          </Fragment>
        )
      })}

      {/* Portfolio properties */}
      {properties.map((property) => {
        const selected =
          selectedProperties.some(
            (selectedProperty) =>
              selectedProperty.id ===
              property.id,
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

                <p>{buildLabel(property)}</p>

                <p>
                  $
                  {property.value.toLocaleString()}
                </p>

                <button
                  type="button"
                  onClick={() =>
                    onToggleProperty(
                      property,
                    )
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