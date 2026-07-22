/**
 * Typed fetch wrapper.
 *
 * Every non-2xx response is turned into an `ApiError` carrying the backend's
 * structured payload, so the UI can always show a message, technical details,
 * a suggested fix and a log path — never a bare "request failed".
 */

import type { ApiErrorPayload, DiagnosticsReport, SettingsResponse, AppSettings } from './types'
import type {
  ExportEntry,
  PreflightResponse,
  RenderJob,
} from './render-types'
import type {
  ShortJob,
  ShortRecord,
  ShortRequest,
  ShortSourceRender,
  ShortSourceTimeline,
  ShortsPreflightResponse,
} from './shorts-types'
import type {
  GenerateResponse,
  TimingResponse,
  TTSProviderStatus,
  Voice,
} from './audio-types'
import type {
  ImageInfo,
  ImportContentResponse,
  MusicTrack,
  Project,
  SceneMotion,
  ProjectResponse,
  ProjectSummary,
  Scene,
  UploadImagesResponse,
} from './project-types'

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

  // --- projects ---
  listProjects: () => request<ProjectSummary[]>('/api/projects'),
  createProject: (name: string, commonName = '', scientificName = '') =>
    request<ProjectResponse>('/api/projects', {
      method: 'POST',
      body: JSON.stringify({ name, commonName, scientificName }),
    }),
  getProject: (slug: string) => request<ProjectResponse>(`/api/projects/${slug}`),
  saveProject: (slug: string, project: Project) =>
    request<ProjectResponse>(`/api/projects/${slug}`, {
      method: 'PUT',
      body: JSON.stringify(project),
    }),
  renameProject: (slug: string, name: string) =>
    request<ProjectResponse>(`/api/projects/${slug}/rename`, {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  duplicateProject: (slug: string, name: string) =>
    request<ProjectResponse>(`/api/projects/${slug}/duplicate`, {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  archiveProject: (slug: string) =>
    request<void>(`/api/projects/${slug}/archive`, { method: 'POST' }),
  unarchiveProject: (slug: string) =>
    request<ProjectResponse>(`/api/projects/${slug}/unarchive`, { method: 'POST' }),
  /** Permanent. The backend requires the slug echoed back as confirmation. */
  deleteProject: (slug: string) =>
    request<void>(`/api/projects/${slug}?confirm=${encodeURIComponent(slug)}`, {
      method: 'DELETE',
    }),

  // --- images ---
  listImages: (slug: string) => request<ImageInfo[]>(`/api/projects/${slug}/images`),
  getMotion: (slug: string) => request<SceneMotion[]>(`/api/projects/${slug}/motion`),
  uploadImages: (slug: string, files: File[]) => {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    return request<UploadImagesResponse>(`/api/projects/${slug}/images`, {
      method: 'POST',
      body: form,
    })
  },
  deleteImage: (slug: string, filename: string) =>
    request<void>(`/api/projects/${slug}/images/${encodeURIComponent(filename)}`, {
      method: 'DELETE',
    }),
  /** Delete every uploaded image and detach it from all units. */
  deleteAllImages: (slug: string) =>
    request<{ removed: number }>(`/api/projects/${slug}/images`, { method: 'DELETE' }),
  assignImage: (slug: string, sceneId: string, imageFile: string | null) =>
    request<ProjectResponse>(`/api/projects/${slug}/scenes/${sceneId}/image`, {
      method: 'POST',
      body: JSON.stringify({ imageFile }),
    }),
  remapImages: (slug: string, force = false) =>
    request<ProjectResponse>(`/api/projects/${slug}/map-images?force=${force}`, { method: 'POST' }),

  // --- content ---
  contentExample: () => request<Record<string, unknown>>('/api/projects/content/example'),
  importContent: (slug: string, content: unknown, replaceScenes = true, mapImages = true) =>
    request<ImportContentResponse>(`/api/projects/${slug}/content`, {
      method: 'POST',
      body: JSON.stringify({ content, replaceScenes, mapImages }),
    }),
  importContentFile: (slug: string, file: File, replaceScenes = true, mapImages = true) => {
    const form = new FormData()
    form.append('file', file)
    return request<ImportContentResponse>(
      `/api/projects/${slug}/content/upload?replace_scenes=${replaceScenes}&map_images=${mapImages}`,
      { method: 'POST', body: form },
    )
  },
  exportContent: (slug: string) =>
    request<Record<string, unknown>>(`/api/projects/${slug}/content/export`),

  // --- scenes ---
  addScene: (slug: string) =>
    request<ProjectResponse>(`/api/projects/${slug}/scenes`, { method: 'POST' }),
  updateScene: (slug: string, scene: Scene) =>
    request<ProjectResponse>(`/api/projects/${slug}/scenes/${scene.id}`, {
      method: 'PUT',
      body: JSON.stringify(scene),
    }),
  duplicateScene: (slug: string, sceneId: string) =>
    request<ProjectResponse>(`/api/projects/${slug}/scenes/${sceneId}/duplicate`, {
      method: 'POST',
    }),
  deleteScene: (slug: string, sceneId: string) =>
    request<ProjectResponse>(`/api/projects/${slug}/scenes/${sceneId}`, { method: 'DELETE' }),
  reorderScenes: (slug: string, sceneIds: string[]) =>
    request<ProjectResponse>(`/api/projects/${slug}/scenes/reorder`, {
      method: 'POST',
      body: JSON.stringify({ sceneIds }),
    }),

  // --- audio / tts ---
  listProviders: () => request<{ providers: TTSProviderStatus[] }>('/api/tts/providers'),
  listVoices: (provider: string) =>
    request<Voice[]>(`/api/tts/voices?provider=${encodeURIComponent(provider)}`),
  generateNarration: (slug: string, unitIds: string[] = [], force = false) =>
    request<GenerateResponse>(`/api/projects/${slug}/audio/generate`, {
      method: 'POST',
      body: JSON.stringify({ unitIds, force }),
    }),
  importAudio: (slug: string, unitId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<GenerateResponse>(`/api/projects/${slug}/audio/import/${unitId}`, {
      method: 'POST',
      body: form,
    })
  },
  getTiming: (slug: string) => request<TimingResponse>(`/api/projects/${slug}/audio/timing`),

  // --- music library ---
  listMusic: (slug: string) => request<MusicTrack[]>(`/api/projects/${slug}/music`),
  uploadMusic: (slug: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ filename: string }>(`/api/projects/${slug}/music`, {
      method: 'POST',
      body: form,
    })
  },
  deleteMusic: (slug: string, filename: string) =>
    request<void>(`/api/projects/${slug}/music/${encodeURIComponent(filename)}`, {
      method: 'DELETE',
    }),

  // --- render ---
  preflight: (slug: string) =>
    request<PreflightResponse>(`/api/projects/${slug}/render/preflight`),
  startRender: (slug: string, quality?: string) =>
    request<RenderJob>(`/api/projects/${slug}/render`, {
      method: 'POST',
      body: JSON.stringify({ quality: quality ?? null }),
    }),
  getJob: (jobId: string) => request<RenderJob>(`/api/jobs/${jobId}`),
  activeJob: () => request<RenderJob | null>('/api/jobs/active'),
  cancelJob: (jobId: string) =>
    request<RenderJob>(`/api/jobs/${jobId}/cancel`, { method: 'POST' }),
  retryJob: (jobId: string) =>
    request<RenderJob>(`/api/jobs/${jobId}/retry`, { method: 'POST' }),
  projectRenders: (slug: string) => request<RenderJob[]>(`/api/projects/${slug}/renders`),
  listExports: (slug: string) => request<ExportEntry[]>(`/api/projects/${slug}/exports`),

  // --- shorts ---
  // Entirely separate from the render endpoints above: a Short is only ever cut
  // from a long render that already finished.
  shortsSources: (slug: string) =>
    request<ShortSourceRender[]>(`/api/projects/${slug}/shorts/sources`),
  shortsTimeline: (slug: string, renderId: string) =>
    request<ShortSourceTimeline>(
      `/api/projects/${slug}/shorts/sources/${encodeURIComponent(renderId)}/timeline`,
    ),
  shortsPreflight: (slug: string, body: ShortRequest) =>
    request<ShortsPreflightResponse>(`/api/projects/${slug}/shorts/preflight`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  createShort: (slug: string, body: ShortRequest) =>
    request<ShortJob>(`/api/projects/${slug}/shorts`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  listShorts: (slug: string) => request<ShortRecord[]>(`/api/projects/${slug}/shorts`),
  deleteShort: (slug: string, shortId: string) =>
    request<{ shortId: string; removed: string[] }>(
      `/api/projects/${slug}/shorts/${encodeURIComponent(shortId)}`,
      { method: 'DELETE' },
    ),
  getShortJob: (jobId: string) => request<ShortJob>(`/api/short-jobs/${jobId}`),
  activeShortJob: (slug?: string) =>
    request<ShortJob | null>(
      slug ? `/api/short-jobs/active?slug=${encodeURIComponent(slug)}` : '/api/short-jobs/active',
    ),
  cancelShortJob: (jobId: string) =>
    request<ShortJob>(`/api/short-jobs/${jobId}/cancel`, { method: 'POST' }),
  retryShortJob: (jobId: string) =>
    request<ShortJob>(`/api/short-jobs/${jobId}/retry`, { method: 'POST' }),

  // --- maintenance ---
  listBackups: (slug: string) => request<string[]>(`/api/projects/${slug}/backups`),
  restoreBackup: (slug: string, name: string) =>
    request<ProjectResponse>(`/api/projects/${slug}/backups/${name}/restore`, { method: 'POST' }),
  cleanDerived: (slug: string) =>
    request<{ removed: number }>(`/api/projects/${slug}/clean-derived`, { method: 'POST' }),
}
