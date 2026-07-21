/**
 * Typed fetch wrapper.
 *
 * Every non-2xx response is turned into an `ApiError` carrying the backend's
 * structured payload, so the UI can always show a message, technical details,
 * a suggested fix and a log path — never a bare "request failed".
 */

import type { ApiErrorPayload, DiagnosticsReport, SettingsResponse, AppSettings } from './types'

export class ApiError extends Error {
  readonly payload: ApiErrorPayload
  readonly status: number

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }

  get code(): string {
    return this.payload.code
  }
  get suggestion(): string {
    return this.payload.suggestion
  }
  get details(): string | null {
    return this.payload.details
  }
  get logPath(): string | null {
    return this.payload.logPath
  }
}

/** Turns any thrown value into something with a usable message. */
export function describeError(error: unknown): ApiErrorPayload {
  if (error instanceof ApiError) return error.payload
  if (error instanceof Error) {
    return {
      code: 'network',
      message: `Could not reach the backend: ${error.message}`,
      details: error.stack ?? null,
      suggestion:
        'Check that the backend is running (./dev.sh starts it), then retry. ' +
        'If it is running, look at the backend log for a startup failure.',
      logPath: null,
      context: {},
    }
  }
  return {
    code: 'unknown',
    message: String(error),
    details: null,
    suggestion: 'Retry the action. If it persists, check the backend log.',
    logPath: null,
    context: {},
  }
}

function isErrorPayload(value: unknown): value is ApiErrorPayload {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { code?: unknown }).code === 'string' &&
    typeof (value as { message?: unknown }).message === 'string'
  )
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let payload: ApiErrorPayload
    try {
      const body: unknown = await response.json()
      payload = isErrorPayload(body)
        ? body
        : {
            code: `http_${response.status}`,
            message: `The server returned HTTP ${response.status} for ${path}.`,
            details: JSON.stringify(body, null, 2),
            suggestion: 'This is unexpected. Check the backend log for details.',
            logPath: null,
            context: {},
          }
    } catch {
      payload = {
        code: `http_${response.status}`,
        message: `The server returned HTTP ${response.status} (${response.statusText}) for ${path}.`,
        details: null,
        suggestion: 'Check that the backend is running and healthy.',
        logPath: null,
        context: {},
      }
    }
    throw new ApiError(response.status, payload)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  health: () => request<{ status: string; app: string }>('/api/health'),
  diagnostics: () => request<DiagnosticsReport>('/api/diagnostics'),
  getSettings: () => request<SettingsResponse>('/api/settings'),
  updateSettings: (settings: AppSettings) =>
    request<SettingsResponse>('/api/settings', { method: 'PUT', body: JSON.stringify(settings) }),
  setSecret: (key: string, value: string | null) =>
    request<SettingsResponse>('/api/settings/secrets', {
      method: 'POST',
      body: JSON.stringify({ key, value }),
    }),
}
