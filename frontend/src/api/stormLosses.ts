import type { Property } from '../types/Property'
import type { Storm } from '../types/Storm'
import type { StormLossResponse } from '../types/StormLoss'

const API_BASE_URL = 'http://127.0.0.1:8000'

export async function getStormLosses(
  properties: Property[],
  stormId: string,
  // A generated storm is not in the API's catalog, so it travels with the request.
  storm?: Storm,
): Promise<StormLossResponse> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/storm-losses`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        properties,
        storm_ids: [stormId],
        ...(storm ? { storms: [storm] } : {}),
      }),
    },
  )

  if (!response.ok) {
    const message = await response.text()

    throw new Error(
      `Storm loss analysis failed: ${response.status} ${message}`,
    )
  }

  return response.json()
}