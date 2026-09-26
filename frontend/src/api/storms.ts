import type { Storm, StormCatalog } from '../types/Storm'

const API_BASE_URL = 'http://127.0.0.1:8000'

export async function getStorm(stormId: string): Promise<Storm> {
  const response = await fetch(`${API_BASE_URL}/api/v1/storm-catalog`)

  if (!response.ok) {
    throw new Error('Failed to load storm catalog')
  }

  const catalog: StormCatalog = await response.json()

  const storm = catalog.storms.find(
    (catalogStorm) => catalogStorm.storm_id === stormId,
  )

  if (!storm) {
    throw new Error(`Storm ${stormId} was not found`)
  }

  return storm
}