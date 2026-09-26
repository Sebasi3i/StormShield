import type { Storm, StormCatalog } from '../types/Storm'

const API_BASE_URL = 'http://127.0.0.1:8000'

export async function getStormCatalog(): Promise<StormCatalog> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/storm-catalog`,
  )

  if (!response.ok) {
    const message = await response.text()

    throw new Error(
      `Failed to load storm catalog: ${response.status} ${message}`,
    )
  }

  return response.json()
}

export function findStorm(
  catalog: StormCatalog,
  stormId: string,
): Storm {
  const storm = catalog.storms.find(
    (catalogStorm) =>
      catalogStorm.storm_id === stormId,
  )

  if (!storm) {
    throw new Error(
      `Storm ${stormId} was not found in the catalog`,
    )
  }

  return storm
}