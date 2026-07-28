/**
 * Publishing state.
 *
 * Its own store, like Shorts: an upload is a different job with a different
 * lifecycle from a render, and nothing here touches `store/render.ts` or
 * `store/shorts.ts`.
 *
 * Two behaviours are worth knowing:
 *
 * * **Drafts are per media file.** Selecting another video swaps the whole
 *   draft; edits to one never leak into another.
 * * **Edits save on a debounce, and the status is visible.** The panel is an
 *   editor, so a save that failed must stay visible as "unsaved" rather than
 *   silently dropping what the user typed.
 */

import { create } from 'zustand'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type {
  DraftResponse,
  MediaItem,
  PublishDraft,
  PublishHistoryEntry,
  PublishJob,
  PublishJobEvent,
  YouTubeConnection,
} from '@/api/publishing-types'
import { attachJobStream, type JobStream } from '@/lib/jobStream'

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

/** Long enough that typing does not fire a request per keystroke. */
const DRAFT_SAVE_DEBOUNCE_MS = 800

export type DraftSaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'error'

interface PublishingState {
  media: MediaItem[]
  selectedMediaId: string | null
  draft: PublishDraft | null
  selectedMedia: MediaItem | null
  sourceChanged: boolean
  sourceChangedReason: string | null
  duplicateOf: PublishHistoryEntry | null

  connection: YouTubeConnection | null
  history: PublishHistoryEntry[]
  job: PublishJob | null
  event: PublishJobEvent | null

  loading: boolean
  busy: boolean
  saveStatus: DraftSaveStatus
  error: ApiErrorPayload | null

  loadConnection: (refresh?: boolean) => Promise<void>
  connectYoutube: () => Promise<void>
  loadMedia: (slug: string) => Promise<void>
  selectMedia: (slug: string, mediaId: string) => Promise<void>
  /** Apply a local edit to the draft and schedule a save. */
  editDraft: (mutate: (draft: PublishDraft) => void) => void
  saveDraft: () => Promise<void>
  refillFromProject: (slug: string) => Promise<void>
  attachThumbnail: (slug: string, file: File) => Promise<void>
  attachCaption: (slug: string, file: File) => Promise<void>
  publish: (slug: string, allowDuplicate: boolean) => Promise<void>
  cancel: () => Promise<void>
  retry: (jobId: string) => Promise<void>
  attach: (slug: string, jobId: string) => void
  detach: () => void
  reattachIfRunning: (slug: string) => Promise<void>
  loadHistory: (slug: string) => Promise<void>
  refreshHistoryEntry: (slug: string, entryId: string) => Promise<void>
  clearError: () => void
}

let stream: JobStream | null = null
let saveTimer: ReturnType<typeof setTimeout> | null = null

function stopStream() {
  stream?.close()
  stream = null
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export const usePublishingStore = create<PublishingState>((set, get) => {
  function applyDraftResponse(response: DraftResponse) {
    set({
      draft: response.draft,
      selectedMedia: response.media,
      selectedMediaId: response.draft.mediaId,
      sourceChanged: response.sourceChanged,
      sourceChangedReason: response.sourceChangedReason,
      duplicateOf: response.duplicateOf,
    })
  }

  return {
    media: [],
    selectedMediaId: null,
    draft: null,
    selectedMedia: null,
    sourceChanged: false,
    sourceChangedReason: null,
    duplicateOf: null,
    connection: null,
    history: [],
    job: null,
    event: null,
    loading: false,
    busy: false,
    saveStatus: 'idle',
    error: null,

    clearError: () => set({ error: null }),

    loadConnection: async (refresh = false) => {
      try {
        set({ connection: await api.youtubeStatus(refresh) })
      } catch (err) {
        set({ error: describeError(err) })
      }
    },

    /**
     * Run the OAuth flow. The browser window is opened by the backend, and the
     * request only returns once the user has finished with it — or refused.
     */
    connectYoutube: async () => {
      set({ busy: true, error: null })
      try {
        set({ connection: await api.connectYoutube() })
      } catch (err) {
        set({ error: describeError(err) })
        await get().loadConnection(false)
      } finally {
        set({ busy: false })
      }
    },

    loadMedia: async (slug) => {
      set({ loading: true })
      try {
        const media = await api.publishingMedia(slug)
        set({ media, error: null })
        const current = get().selectedMediaId
        if (!current || !media.some((item) => item.mediaId === current)) {
          const first = media.find((item) => item.recommended) ?? media[0]
          if (first) await get().selectMedia(slug, first.mediaId)
          else set({ selectedMediaId: null, draft: null, selectedMedia: null })
        }
      } catch (err) {
        set({ error: describeError(err) })
      } finally {
        set({ loading: false })
      }
    },

    selectMedia: async (slug, mediaId) => {
      if (saveTimer) clearTimeout(saveTimer)
      saveTimer = null
      set({ saveStatus: 'idle' })
      try {
        applyDraftResponse(await api.getPublishDraft(slug, mediaId))
        set({ error: null })
      } catch (err) {
        set({ error: describeError(err) })
      }
    },

    editDraft: (mutate) => {
      const current = get().draft
      if (!current) return
      const next = clone(current)
      mutate(next)
      set({ draft: next, saveStatus: 'dirty' })

      if (saveTimer) clearTimeout(saveTimer)
      saveTimer = setTimeout(() => {
        void get().saveDraft()
      }, DRAFT_SAVE_DEBOUNCE_MS)
    },

    saveDraft: async () => {
      const { draft } = get()
      if (!draft) return
      if (saveTimer) {
        clearTimeout(saveTimer)
        saveTimer = null
      }
      set({ saveStatus: 'saving' })
      try {
        applyDraftResponse(
          await api.savePublishDraft(draft.projectSlug, draft.mediaId, draft),
        )
        set({ saveStatus: 'saved', error: null })
      } catch (err) {
        // Stay dirty: the edit only exists in memory until a save succeeds.
        set({ saveStatus: 'error', error: describeError(err) })
      }
    },

    /** Throw away the YouTube text and take the project's metadata again. */
    refillFromProject: async (slug) => {
      const { selectedMediaId } = get()
      if (!selectedMediaId) return
      try {
        const project = await api.getProject(slug)
        const metadata = project.project.metadata
        get().editDraft((draft) => {
          draft.common.title = metadata.videoTitle || project.project.name
          draft.common.description = metadata.description
          draft.common.tags = [...metadata.tags]
          draft.common.thumbnailText = metadata.thumbnailText
          draft.common.thumbnailPrompt = metadata.thumbnailPrompt
          draft.youtube.title = (metadata.videoTitle || project.project.name).slice(0, 100)
          draft.youtube.description = metadata.description
          draft.youtube.tags = [...metadata.tags]
        })
        await get().saveDraft()
      } catch (err) {
        set({ error: describeError(err) })
      }
    },

    attachThumbnail: async (slug, file) => {
      set({ busy: true })
      try {
        const asset = await api.uploadPublishThumbnail(slug, file)
        get().editDraft((draft) => {
          draft.youtube.thumbnailFile = asset.filename
        })
        await get().saveDraft()
        set({ error: null })
      } catch (err) {
        set({ error: describeError(err) })
      } finally {
        set({ busy: false })
      }
    },

    attachCaption: async (slug, file) => {
      set({ busy: true })
      try {
        const asset = await api.uploadPublishCaption(slug, file)
        get().editDraft((draft) => {
          draft.youtube.captionFile = asset.filename
          draft.youtube.captionSource = 'asset'
          draft.youtube.uploadCaptions = true
        })
        await get().saveDraft()
        set({ error: null })
      } catch (err) {
        set({ error: describeError(err) })
      } finally {
        set({ busy: false })
      }
    },

    publish: async (slug, allowDuplicate) => {
      const { selectedMediaId, saveStatus } = get()
      if (!selectedMediaId) return
      set({ busy: true, error: null, event: null })
      try {
        // Never upload text the backend has not seen.
        if (saveStatus === 'dirty' || saveStatus === 'error') await get().saveDraft()
        const job = await api.publishToYoutube(slug, {
          mediaId: selectedMediaId,
          allowDuplicate,
        })
        set({ job })
        if (TERMINAL.has(job.status)) await get().loadHistory(slug)
        else get().attach(slug, job.id)
      } catch (err) {
        set({ error: describeError(err) })
      } finally {
        set({ busy: false })
      }
    },

    cancel: async () => {
      const { job } = get()
      if (!job) return
      try {
        set({ job: await api.cancelPublishJob(job.id) })
      } catch (err) {
        set({ error: describeError(err) })
      }
    },

    retry: async (jobId) => {
      set({ busy: true, error: null })
      try {
        const job = await api.retryPublishJob(jobId)
        set({ job, event: null })
        if (TERMINAL.has(job.status)) await get().loadHistory(job.projectSlug)
        else get().attach(job.projectSlug, job.id)
      } catch (err) {
        set({ error: describeError(err) })
      } finally {
        set({ busy: false })
      }
    },

    attach: (slug, jobId) => {
      stopStream()
      stream = attachJobStream<PublishJobEvent, PublishJob>({
        url: `/api/publishing/jobs/${jobId}/events`,
        poll: () => api.getPublishJob(jobId),
        onEvent: (event) => set({ event }),
        onPoll: (job) => set({ job }),
        isTerminalEvent: (event) => TERMINAL.has(event.status),
        isTerminalJob: (job) => TERMINAL.has(job.status),
        onFinish: () => {
          void (async () => {
            try {
              set({ job: await api.getPublishJob(jobId) })
              await get().loadHistory(slug)
              await get().loadMedia(slug)
            } catch (err) {
              set({ error: describeError(err) })
            }
          })()
        },
      })
    },

    detach: () => {
      stopStream()
      if (saveTimer) clearTimeout(saveTimer)
      saveTimer = null
      set({ event: null })
    },

    /** After a reload, pick up an upload that is still running. */
    reattachIfRunning: async (slug) => {
      try {
        const active = await api.activePublishJob(slug)
        if (active && active.projectSlug === slug) {
          set({ job: active })
          get().attach(slug, active.id)
        }
      } catch {
        // An unreachable backend is reported by the page itself.
      }
    },

    loadHistory: async (slug) => {
      try {
        set({ history: await api.publishHistory(slug) })
      } catch (err) {
        set({ error: describeError(err) })
      }
    },

    refreshHistoryEntry: async (slug, entryId) => {
      set({ busy: true })
      try {
        const updated = await api.refreshPublishHistoryEntry(slug, entryId)
        set({
          history: get().history.map((entry) =>
            entry.entryId === updated.entryId ? updated : entry,
          ),
          error: null,
        })
      } catch (err) {
        set({ error: describeError(err) })
      } finally {
        set({ busy: false })
      }
    },
  }
})

/** Flush a pending draft save. Call before navigating away or uploading. */
export async function flushPendingDraftSave(): Promise<void> {
  const { saveStatus, saveDraft } = usePublishingStore.getState()
  if (saveStatus === 'dirty' || saveStatus === 'error') await saveDraft()
}
