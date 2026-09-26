import type {
  GenerateStormsRequest,
  GeneratedStormCatalog,
  Storm,
  StormCatalog,
} from '../types/Storm'

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

/*
 * Runs the hurricane simulator on the API from one starting point. The first
 * call loads the simulator, so it takes a few seconds; later calls are quick.
 */
export async function generateStorms(
  request: GenerateStormsRequest,
): Promise<GeneratedStormCatalog> {
  const response = await fetch(`${API_BASE_URL}/api/v1/storms/generate`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(await errorMessage(response, 'Storm generation failed'))
  }

  return response.json()
}

/*
 * The API explains a rejected start (over land, too far from where storms
 * form) in detail.message, and a rejected field in FastAPI's detail list.
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
