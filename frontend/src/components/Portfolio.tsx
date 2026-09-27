import type { Property } from '../types/Property'

interface PortfolioProps {
  properties: Property[]
  onRemoveProperty: (property: Property) => void
  onAnalyzePortfolio: () => void
  analysisAvailable: boolean
}

function Portfolio({
  properties,
  onRemoveProperty,
  onAnalyzePortfolio,
  analysisAvailable,
}: PortfolioProps) {
  const totalValue = properties.reduce(
    (total, property) => total + property.value,
    0,
  )

  return (
    <aside className="portfolio">
      <div className="portfolio-header">
        <p className="portfolio-label">YOUR PORTFOLIO</p>
        <h2>Property Portfolio</h2>
      </div>

      <div className="portfolio-summary">
        <div className="summary-card">
          <span>Properties</span>
          <strong>{properties.length}</strong>
        </div>

        <div className="summary-card">
          <span>Total Value</span>
          <strong>
            ${totalValue.toLocaleString()}
          </strong>
        </div>
      </div>

      <div className="portfolio-properties">
        <h3>Selected Properties</h3>

        {properties.length === 0 ? (
          <div className="empty-portfolio">
            <p>No properties selected.</p>
            <span>
              Select properties from the map to build your portfolio.
            </span>
          </div>
        ) : (
          <div className="property-list">
            {properties.map((property) => (
              <div
                className="portfolio-property"
                key={property.id}
              >
                <div>
                  <strong>{property.address}</strong>

                  <p>
                    {property.city}, FL
                  </p>

                  <span>
                    {property.county} County
                  </span>

                  <p className="portfolio-property-value">
                    ${property.value.toLocaleString()}
                  </p>
                </div>

                <button
                  type="button"
                  className="remove-property"
                  onClick={() => onRemoveProperty(property)}
                  aria-label={`Remove ${property.address}`}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <button
        type="button"
        className="analyze-button"
        disabled={
          properties.length === 0 ||
          !analysisAvailable
        }
        onClick={onAnalyzePortfolio}
      >
        Analyze Portfolio Risk
      </button>
    </aside>
  )
}

export default Portfolio