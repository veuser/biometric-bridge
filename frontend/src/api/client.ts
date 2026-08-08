/**
 * Fetch wrapper for the bridge API.
 *
 * Auth is an httpOnly session cookie, so there is no token to store or attach —
 * `credentials: 'include'` is the whole of it. That also means an XSS bug in this
 * app cannot exfiltrate a session.
 */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
  }
}

/** Set by the app shell so a 401 anywhere sends the operator back to sign-in. */
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  })

  if (response.status === 401) {
    onUnauthorized?.()
    throw new ApiError(401, 'Your session has expired. Please sign in again.')
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readError(response))
  }

  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

/**
 * FastAPI reports validation failures as a list of field errors; showing the raw
 * array to an operator is useless, so it is flattened into a sentence.
 */
async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json()
    const detail = body?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item: { loc?: (string | number)[]; msg?: string }) => {
          const field = item.loc?.filter((p) => p !== 'body').join('.')
          return field ? `${field}: ${item.msg}` : item.msg
        })
        .join('; ')
    }
  } catch {
    /* fall through */
  }
  return `Request failed (${response.status}).`
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
