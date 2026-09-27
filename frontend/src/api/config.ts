/*
 * Where the API lives. Set VITE_API_BASE_URL (for example in frontend/.env.local)
 * to point the dashboard at a deployed backend; the default is the local dev server.
 */
export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
