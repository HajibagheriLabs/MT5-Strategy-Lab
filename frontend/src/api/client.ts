/** The API's own words for a failure, with the HTTP status that came with them. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function detail(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const value = (body as { detail: unknown }).detail
    if (typeof value === 'string') return value
    // Request validation lists each field that was wrong.
    if (Array.isArray(value)) {
      return value
        .map((item: { loc?: unknown[]; msg?: string }) => {
          const field = (item.loc ?? []).filter((part) => part !== 'body').join('.')
          return field ? `${field}: ${item.msg}` : (item.msg ?? '')
        })
        .join('; ')
    }
  }
  return fallback
}

async function request<T>(method: string, path: string, body?: BodyInit | object): Promise<T> {
  const init: RequestInit = { method, headers: { Accept: 'application/json' } }
  if (body instanceof FormData) {
    init.body = body
  } else if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { ...init.headers, 'Content-Type': 'application/json' }
  }
  let response: Response
  try {
    response = await fetch(`/api${path}`, init)
  } catch {
    throw new ApiError(0, 'StrategyLab is not answering. Check that the server is running (python tasks.py dev).')
  }
  if (response.status === 204) return undefined as T
  const text = await response.text()
  let parsed: unknown
  try {
    parsed = text ? JSON.parse(text) : undefined
  } catch {
    parsed = text
  }
  if (!response.ok) {
    throw new ApiError(response.status, detail(parsed, `The server answered ${response.status}.`))
  }
  return parsed as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: BodyInit | object) => request<T>('POST', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
  text: async (path: string): Promise<string> => {
    const response = await fetch(`/api${path}`)
    if (!response.ok) throw new ApiError(response.status, detail(await response.json().catch(() => null), `The server answered ${response.status}.`))
    return response.text()
  },
}
