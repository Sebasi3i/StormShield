import { API_BASE_URL } from './config'
import type {
  InsurerCompareRequest,
  InsurerCompareResponse,
  InsurerDemo,
} from '../types/Insurer'

/*
 * The API explains a rejected request in detail.message (service rejections) or in
 * FastAPI's detail list (schema rejections).
 */
async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json()
    const detail = body?.detail

    if (typeof detail?.message === 'string') {
      return detail.message
    }

    if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') {
      const location: unknown[] = detail[0].loc ?? []

      return `${location.slice(1).join('.')}: ${detail[0].msg}`
    }
  } catch {
    // Not JSON: fall back to the status code below.
  }

  return `${fallback} (${response.status})`
}

export async function getInsurerDemo(): Promise<InsurerDemo> {
  const response = await fetch(`${API_BASE_URL}/api/v1/insurer/demo`)

  if (!response.ok) {
    throw new Error(await errorMessage(response, 'Failed to load the sample insurer'))
  }

  return response.json()
}

async function post(
  path: string,
  request: InsurerCompareRequest,
  fallback: string,
): Promise<InsurerCompareResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(await errorMessage(response, fallback))
  }

  return response.json()
}

export function compareInsurer(request: InsurerCompareRequest) {
  return post('/api/v1/insurer/compare', request, 'Comparison failed')
}

export function optimizeInsurer(request: InsurerCompareRequest) {
  return post('/api/v1/insurer/optimize', request, 'Optimization failed')
}
