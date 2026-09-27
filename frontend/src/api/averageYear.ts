import { API_BASE_URL } from './config'
import type { Property } from '../types/Property'
import type { AverageYearResponse } from '../types/AverageYear'

/*
 * An average year for the selected properties: what storms cost per year on average
 * over thousands of simulated storms, and what each upgrade would save per year.
 * Only the demo properties are covered; the server says so for any other id.
 */
export async function getAverageYear(properties: Property[]): Promise<AverageYearResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/storm-losses/average-year`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ properties }),
  })

  if (!response.ok) {
    let message = `Average-year figures unavailable (${response.status})`

    try {
      const body = await response.json()

      if (typeof body?.detail?.message === 'string') message = body.detail.message
    } catch {
      // keep the status message
    }

    throw new Error(message)
  }

  return response.json()
}
