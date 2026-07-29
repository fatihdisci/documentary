/**
 * Typed fetch wrapper.
 *
 * Every non-2xx response is turned into an `ApiError` carrying the backend's
 * structured payload, so the UI can always show a message, technical details,
 * a suggested fix and a log path — never a bare "request failed".
 */

import type {
  ApiErrorPayload,
  AppSettings,
  BundleContents,
  BundleImportResult,
  DiagnosticsReport,
  ExportedBundle,
  SettingsResponse,
} from './types'
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
  AssetUploadResponse,
  ClientSecretUploadResponse,
  DraftResponse,
  MediaHostStatus,
  MediaItem,
  MetaAppCredentials,
  MetaConnection,
  OAuthStart,
  ObjectStorageSettings,
  PublishDraft,
  PublishHistoryEntry,
  PublishJob,
  PublishRequest,
  SocialPlatform,
  TikTokAppCredentials,
  TikTokConnection,
  YouTubeConnection,
} from './publishing-types'
import type {
  GenerateResponse,
  KokoroInfo,
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
      message: `Uygulamaya ulaşılamadı: ${error.message}`,
      details: error.stack ?? null,
      suggestion:
        'Uygulamanın arka planda çalıştığından emin olun, sonra tekrar deneyin. ' +
        'Çalışıyorsa kayıt dosyasına bakın.',
      logPath: null,
      context: {},
    }
  }
  return {
    code: 'unknown',
    message: String(error),
    details: null,
    suggestion: 'Tekrar deneyin. Sürerse kayıt dosyasına bakın.',
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

/** Build the same `ApiError` `request()` throws, for callers that fetch raw. */
async function toApiError(response: Response, path: string): Promise<ApiError> {
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
  return new ApiError(response.status, payload)
}

/** The filename a `Content-Disposition: attachment` header names, if any. */
function filenameFrom(response: Response): string | null {
  const header = response.headers.get('Content-Disposition')
  const match = header?.match(/filename="?([^";]+)"?/)
  return match?.[1] ?? null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })

  if (!response.ok) throw await toApiError(response, path)

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

  // --- moving an installation to another computer ---
  /**
   * Ask for a sealed bundle.
   *
   * Comes back as bytes, not JSON: the file is saved straight to disk and its
   * contents never sit in a variable this page could read.
   */
  exportSettingsBundle: async (
    passphrase: string,
    includeCredentials: boolean,
  ): Promise<ExportedBundle> => {
    const response = await fetch('/api/settings/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passphrase, includeCredentials }),
    })
    if (!response.ok) throw await toApiError(response, '/api/settings/export')
    return {
      blob: await response.blob(),
      filename: filenameFrom(response) ?? 'evb-ayarlar.evbkey',
      contents: {
        secrets: Number(response.headers.get('X-Evb-Bundle-Secrets') ?? 0),
        credentialFiles: Number(response.headers.get('X-Evb-Bundle-Credential-Files') ?? 0),
      },
    }
  },
  /** What a bundle holds, before the user has to type anything. */
  inspectSettingsBundle: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<BundleContents>('/api/settings/import/inspect', {
      method: 'POST',
      body: form,
    })
  },
  importSettingsBundle: (
    file: File,
    passphrase: string,
    options: { overwrite: boolean; includePaths: boolean },
  ) => {
    const form = new FormData()
    form.append('file', file)
    form.append('passphrase', passphrase)
    form.append('overwrite', String(options.overwrite))
    form.append('includePaths', String(options.includePaths))
    return request<BundleImportResult>('/api/settings/import', {
      method: 'POST',
      body: form,
    })
  },

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
  getKokoroInfo: () => request<KokoroInfo>('/api/tts/kokoro/info'),
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

  // --- publishing ---
  // The YouTube connection is per computer, so it lives outside the project
  // routes. Everything below it belongs to one project.
  youtubeStatus: (refresh = false) =>
    request<YouTubeConnection>(`/api/publishing/youtube/status?refresh=${refresh}`),
  uploadYoutubeClientSecret: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ClientSecretUploadResponse>('/api/publishing/youtube/client-secret', {
      method: 'POST',
      body: form,
    })
  },
  selectYoutubeClientSecret: (fileName: string) =>
    request<YouTubeConnection>('/api/publishing/youtube/client-secret/select', {
      method: 'POST',
      body: JSON.stringify({ fileName }),
    }),
  connectYoutube: () =>
    request<YouTubeConnection>('/api/publishing/youtube/connect', { method: 'POST' }),
  disconnectYoutube: () =>
    request<YouTubeConnection>('/api/publishing/youtube/disconnect', { method: 'DELETE' }),

  publishingMedia: (slug: string) =>
    request<MediaItem[]>(`/api/projects/${slug}/publishing/media`),
  getPublishDraft: (slug: string, mediaId: string) =>
    request<DraftResponse>(
      `/api/projects/${slug}/publishing/drafts/${encodeURIComponent(mediaId)}`,
    ),
  savePublishDraft: (slug: string, mediaId: string, draft: PublishDraft) =>
    request<DraftResponse>(
      `/api/projects/${slug}/publishing/drafts/${encodeURIComponent(mediaId)}`,
      { method: 'PUT', body: JSON.stringify(draft) },
    ),
  uploadPublishThumbnail: (slug: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<AssetUploadResponse>(
      `/api/projects/${slug}/publishing/assets/thumbnail`,
      { method: 'POST', body: form },
    )
  },
  uploadPublishCaption: (slug: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<AssetUploadResponse>(
      `/api/projects/${slug}/publishing/assets/caption`,
      { method: 'POST', body: form },
    )
  },
  publishToYoutube: (slug: string, body: PublishRequest) =>
    request<PublishJob>(`/api/projects/${slug}/publishing/youtube`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  /** Queue one upload to Instagram, Facebook or TikTok. Its own job each time. */
  publishToPlatform: (slug: string, platform: SocialPlatform, body: PublishRequest) =>
    request<PublishJob>(`/api/projects/${slug}/publishing/${platform}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // --- Meta (Instagram + Facebook), one connection for both ---
  metaStatus: () => request<MetaConnection>('/api/publishing/meta/status'),
  /** Write-only: no endpoint returns the App ID or the App Secret. */
  saveMetaAppCredentials: (body: MetaAppCredentials) =>
    request<MetaConnection>('/api/publishing/meta/app-credentials', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  clearMetaAppCredentials: () =>
    request<MetaConnection>('/api/publishing/meta/app-credentials', { method: 'DELETE' }),
  /** Returns the URL to open; Meta redirects back to the backend's callback. */
  startMetaConnect: () =>
    request<OAuthStart>('/api/publishing/meta/connect', { method: 'POST' }),
  selectMetaPage: (pageId: string) =>
    request<MetaConnection>('/api/publishing/meta/page', {
      method: 'POST',
      body: JSON.stringify({ pageId }),
    }),
  disconnectMeta: () =>
    request<MetaConnection>('/api/publishing/meta/disconnect', { method: 'DELETE' }),

  // --- TikTok ---
  tiktokStatus: (refresh = false) =>
    request<TikTokConnection>(`/api/publishing/tiktok/status?refresh=${refresh}`),
  saveTiktokAppCredentials: (body: TikTokAppCredentials) =>
    request<TikTokConnection>('/api/publishing/tiktok/app-credentials', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  clearTiktokAppCredentials: () =>
    request<TikTokConnection>('/api/publishing/tiktok/app-credentials', { method: 'DELETE' }),
  startTiktokConnect: () =>
    request<OAuthStart>('/api/publishing/tiktok/connect', { method: 'POST' }),
  disconnectTiktok: () =>
    request<TikTokConnection>('/api/publishing/tiktok/disconnect', { method: 'DELETE' }),

  // --- temporary media hosting (only Instagram and Facebook need it) ---
  mediaHostStatus: () => request<MediaHostStatus>('/api/publishing/media-host/status'),
  saveMediaHostSettings: (body: ObjectStorageSettings) =>
    request<MediaHostStatus>('/api/publishing/media-host/settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  clearMediaHostKeys: () =>
    request<MediaHostStatus>('/api/publishing/media-host/keys', { method: 'DELETE' }),
  publishHistory: (slug: string) =>
    request<PublishHistoryEntry[]>(`/api/projects/${slug}/publishing/history`),
  refreshPublishHistoryEntry: (slug: string, entryId: string) =>
    request<PublishHistoryEntry>(
      `/api/projects/${slug}/publishing/history/${encodeURIComponent(entryId)}/refresh`,
      { method: 'POST' },
    ),
  getPublishJob: (jobId: string) => request<PublishJob>(`/api/publishing/jobs/${jobId}`),
  activePublishJob: (slug?: string) =>
    request<PublishJob | null>(
      slug
        ? `/api/publishing/jobs/active?slug=${encodeURIComponent(slug)}`
        : '/api/publishing/jobs/active',
    ),
  cancelPublishJob: (jobId: string) =>
    request<PublishJob>(`/api/publishing/jobs/${jobId}/cancel`, { method: 'POST' }),
  retryPublishJob: (jobId: string) =>
    request<PublishJob>(`/api/publishing/jobs/${jobId}/retry`, { method: 'POST' }),

  // --- maintenance ---
  listBackups: (slug: string) => request<string[]>(`/api/projects/${slug}/backups`),
  restoreBackup: (slug: string, name: string) =>
    request<ProjectResponse>(`/api/projects/${slug}/backups/${name}/restore`, { method: 'POST' }),
  cleanDerived: (slug: string) =>
    request<{ removed: number }>(`/api/projects/${slug}/clean-derived`, { method: 'POST' }),
}
