/**
 * Render job state, driven by a server-sent event stream.
 *
 * The stream is the source of truth while a render runs. If it drops — a
 * reload, a sleeping laptop — the store falls back to polling and reattaches,
 * so a render in progress is never lost from the UI.
 */

import { create } from 'zustand'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { ExportEntry, JobEvent, PreflightResponse, RenderJob } from '@/api/render-types'

interface RenderState {
  preflight: PreflightResponse | null
  job: RenderJob | null
  event: JobEvent | null
  history: RenderJob[]
  exports: ExportEntry[]
  error: ApiErrorPayload | null
  busy: boolean

  loadPreflight: (slug: string) => Promise<void>
  loadHistory: (slug: string) => Promise<void>
  start: (slug: string, quality?: string) => Promise<void>
  cancel: () => Promise<void>
  retry: (jobId: string) => Promise<void>
  attach: (jobId: string) => void
  detach: () => void
  reattachIfRunning: (slug: string) => Promise<void>
  clearError: () => void
}

let source: EventSource | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null

function stopStream() {
  source?.close()
  source = null
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

export const useRenderStore = create<RenderState>((set, get) => ({
  preflight: null,
  job: null,
  event: null,
  history: [],
  exports: [],
  error: null,
  busy: false,

  clearError: () => set({ error: null }),

  loadPreflight: async (slug) => {
    try {
      set({ preflight: await api.preflight(slug), error: null })
    } catch (err) {
      set({ error: describeError(err) })
    }
  },

  loadHistory: async (slug) => {
    try {
      const [history, exports] = await Promise.all([
        api.projectRenders(slug),
        api.listExports(slug),
      ])
      set({ history, exports })
    } catch (err) {
      set({ error: describeError(err) })
    }
  },

  start: async (slug, quality) => {
    set({ busy: true, error: null, event: null })
    try {
      const job = await api.startRender(slug, quality)
      set({ job })
      get().attach(job.id)
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
      set({ job: await api.cancelJob(job.id) })
    } catch (err) {
      set({ error: describeError(err) })
    }
  },

  retry: async (jobId) => {
    set({ busy: true, error: null })
    try {
      const job = await api.retryJob(jobId)
      set({ job, event: null })
      get().attach(job.id)
    } catch (err) {
      set({ error: describeError(err) })
    } finally {
      set({ busy: false })
    }
  },

  attach: (jobId) => {
    stopStream()

    const finish = async () => {
      stopStream()
      try {
        const job = await api.getJob(jobId)
        set({ job })
        await get().loadHistory(job.projectSlug)
        await get().loadPreflight(job.projectSlug)
      } catch (err) {
        set({ error: describeError(err) })
      }
    }

    source = new EventSource(`/api/jobs/${jobId}/events`)

    source.onmessage = (message) => {
      const event = JSON.parse(message.data as string) as JobEvent
      set({ event })
      if (TERMINAL.has(event.status)) void finish()
    }

    source.onerror = () => {
      // The stream dropped. Rather than losing sight of a running render,
      // fall back to polling until it reaches a terminal state.
      source?.close()
      source = null
      if (pollTimer) return
      pollTimer = setInterval(() => {
        void api
          .getJob(jobId)
          .then((job) => {
            set({ job })
            if (TERMINAL.has(job.status)) void finish()
          })
          .catch(() => undefined)
      }, 2000)
    }
  },

  detach: () => {
    stopStream()
    set({ event: null })
  },

  /** After a page reload, pick up a render that is still going. */
  reattachIfRunning: async (slug) => {
    try {
      const active = await api.activeJob()
      if (active && active.projectSlug === slug) {
        set({ job: active })
        get().attach(active.id)
      }
    } catch {
      // An unreachable backend is reported by the pages themselves.
    }
  },
}))
