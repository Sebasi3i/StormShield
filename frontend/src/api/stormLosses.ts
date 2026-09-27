import { API_BASE_URL } from './config'
import type { Property } from '../types/Property'
import type { Storm } from '../types/Storm'
import type { StormLossResponse } from '../types/StormLoss'

/*
 * Prices every storm in stormIds against the properties in one run. Catalog
 * storms are known to the API by id; generated storms are not, so they travel
 * with the request.
 */
export async function getStormLosses(
  properties: Property[],
  stormIds: string[],
  storms?: Storm[],
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
        storm_ids: stormIds,
        ...(storms && storms.length > 0 ? { storms } : {}),
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

/*
 * The rows of a multi-storm run that belong to one storm, in the same
 * envelope, so single-storm views can read a batch result unchanged.
 */
export function lossesForStorm(
  losses: StormLossResponse,
  stormId: string,
): StormLossResponse {
  return {
    ...losses,
    storm_ids: [stormId],
    rows: losses.rows.filter((row) => row.storm_id === stormId),
  }
}
