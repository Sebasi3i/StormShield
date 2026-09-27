import { API_BASE_URL } from './config'
import type {
  FloridaStormBatch,
  GenerateFloridaStormsRequest,
  StormCatalog,
} from '../types/Storm'

/*
 * The stored catalog: the selectable storm tracks, with everything needed to
 * draw and animate them.
 */
export async function getStormCatalog(): Promise<StormCatalog> {
  const response = await fetch(`${API_BASE_URL}/api/v1/storm-catalog`)

  if (!response.ok) {
    throw new Error('Failed to load storm catalog')
  }

  return response.json()
}

/*
 * A batch of storms from a random starting point that reaches Florida. The API
 * searches starting points until enough of the batch cross Florida at Category
 * 3 or stronger, so a call takes a few seconds; the first one also loads the
 * simulator. The same seed always returns the same batch within a season year.
 */
export async function generateFloridaStorms(
  request: GenerateFloridaStormsRequest,
): Promise<FloridaStormBatch> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/storms/generate-florida`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    },
  )

  if (!response.ok) {
    throw new Error(await errorMessage(response, 'Storm generation failed'))
  }

  return response.json()
}

/*
 * The API explains a failed search in detail.message, and a rejected field in
 * FastAPI's detail list.
 */
async function errorMessage(
  response: Response,
  fallback: string,
): Promise<string> {
  try {
    const body = await response.json()
    const detail = body?.detail

    if (typeof detail?.message === 'string') {
      return detail.message
    }

    if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') {
      const location: unknown[] = detail[0].loc ?? []
      const field = location[location.length - 1]

      return field ? `${String(field)}: ${detail[0].msg}` : detail[0].msg
    }
  } catch {
    // Not JSON: fall back to the status code below.
  }

  return `${fallback} (${response.status})`
}
