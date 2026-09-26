import { MapContainer, Marker, Popup, TileLayer } from 'react-leaflet'
import type { Property } from '../types/Property'
import 'leaflet/dist/leaflet.css'

interface PropertyMapProps {
  properties: Property[]
  selectedProperties: Property[]
  onToggleProperty: (property: Property) => void
}

function PropertyMap({
  properties,
  selectedProperties,
  onToggleProperty,
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