/**
 * Shorts state.
 *
 * Deliberately its own store. A Short and a long render are different jobs with
 * different lifecycles, and mixing them would mean a Short's progress could
 * overwrite a render's — so nothing here touches `store/render.ts`.
 *
 * The live-progress behaviour is the same one the render store established
 * (SSE first, polling if the stream drops) via the shared `attachJobStream`
 * helper, so a Short in progress survives a page reload.
 */

import { create } from 'zustand'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type {
  ShortCaptionMode,
  ShortCaptionPreset,
  ShortJob,
  ShortJobEvent,
  ShortRecord,
  ShortRequest,
  ShortSourceRender,
  ShortSourceTimeline,
  ShortsPreflightResponse,
} from '@/api/shorts-types'
import { captionSupportOf } from '@/api/shorts-types'
import { attachJobStream, type JobStream } from '@/lib/jobStream'
import type { Selection } from '@/lib/shortsPlan'

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

/** Debounce for the authoritative preflight; a trim drag emits a lot of these. */
const PREFLIGHT_DEBOUNCE_MS = 400

interface ShortsState {
  sources: ShortSourceRender[]
  selectedRenderId: string | null
  timeline: ShortSourceTimeline | null
  /** Selected sections, in the order the user clicked them. */
  selection: Selection[]
  /** Where this Short's captions come from. Defaults to the legacy behaviour. */
  captionMode: ShortCaptionMode
  captionPreset: ShortCaptionPreset
  preflight: ShortsPreflightResponse | null
  job: ShortJob | null
  event: ShortJobEvent | null
  history: ShortRecord[]
  error: ApiErrorPayload | null
  loading: boolean
  busy: boolean

  loadSources: (slug: string) => Promise<void>
  selectSource: (slug: string, renderId: string) => Promise<void>
  toggleSection: (slug: string, unitId: string) => void
  moveSelection: (slug: string, unitId: string, direction: -1 | 1) => void
  removeSelection: (slug: string, unitId: string) => void
  clearSelection: (slug: string) => void
  setTrim: (slug: string, unitId: string, edge: 'start' | 'end', value: number | null) => void
  setCaptionMode: (slug: string, mode: ShortCaptionMode) => void
  setCaptionPreset: (slug: string, preset: ShortCaptionPreset) => void
  refreshPreflight: (slug: string) => Promise<void>
  start: (slug: string) => Promise<void>
  cancel: () => Promise<void>
  retry: (jobId: string) => Promise<void>
  attach: (slug: string, jobId: string) => void
  detach: () => void
  reattachIfRunning: (slug: string) => Promise<void>
  loadHistory: (slug: string) => Promise<void>
  remove: (slug: string, shortId: string) => Promise<void>
  clearError: () => void
}

let stream: JobStream | null = null
let preflightTimer: ReturnType<typeof setTimeout> | null = null

function stopStream() {
  stream?.close()
  stream = null
}

function cancelPreflight() {
  if (preflightTimer) clearTimeout(preflightTimer)
  preflightTimer = null
}

export function requestFor(
  renderId: string,
  selection: Selection[],
  captions?: { mode: ShortCaptionMode; preset: ShortCaptionPreset },
): ShortRequest {
  const request: ShortRequest = {
    sourceRenderId: renderId,
    segments: selection.map((item) => ({
      unitId: item.unitId,
      startSeconds: item.startSeconds,
      endSeconds: item.endSeconds,
    })),
  }
  // Only sent when it is not the default, so a legacy Short's request — and the
  // cache key derived from it — stays byte-for-byte what it always was.
  if (captions && captions.mode !== 'source-burned-in') {
    request.captionMode = captions.mode
    if (captions.mode === 'shorts-native') request.captionStyle = { preset: captions.preset }
  }
  return request
}

export const useShortsStore = create<ShortsState>((set, get) => {
  /** Re-runs preflight after the user stops fiddling. */
  function schedulePreflight(slug: string) {
    cancelPreflight()
    if (get().selection.length === 0) {
      set({ preflight: null })
      return
    }
    preflightTimer = setTimeout(() => {
      void get().refreshPreflight(slug)
    }, PREFLIGHT_DEBOUNCE_MS)
  }

  return {
    sources: [],
    selectedRenderId: null,
    timeline: null,
    selection: [],
    captionMode: 'source-burned-in',
    captionPreset: 'standard',
    preflight: null,
    job: null,
    event: null,
    history: [],
    error: null,
    loading: false,
    busy: false,

    clearError: () => set({ error: null }),

    loadSources: async (slug) => {
      set({ loading: true })
      try {
        const sources = await api.shortsSources(slug)
        set({ sources, error: null })
        // Auto-select the newest usable render, so the page is useful on arrival.
        const current = get().selectedRenderId
        const stillThere = sources.some((s) => s.renderId === current)
        if (!stillThere) {
          const first = sources.find((s) => s.usable)
          if (first) await get().selectSource(slug, first.renderId)
          else set({ selectedRenderId: null, timeline: null, selection: [], preflight: null })
        }
      } catch (err) {
        set({ error: describeError(err) })
      } finally {
        set({ loading: false })
      }
    },

    selectSource: async (slug, renderId) => {
      cancelPreflight()
      // Switching to a render that cannot do large captions drops back to the
      // legacy mode rather than leaving a selection the backend would reject.
      const source = get().sources.find((entry) => entry.renderId === renderId)
      const mode = captionSupportOf(source).nativeAvailable ? get().captionMode : 'source-burned-in'
      set({
        selectedRenderId: renderId,
        selection: [],
        preflight: null,
        timeline: null,
        captionMode: mode,
      })
      try {
        set({ timeline: await api.shortsTimeline(slug, renderId), error: null })
      } catch (err) {
        set({ error: describeError(err) })
      }
    },

    toggleSection: (slug, unitId) => {
      const { selection } = get()
      const next = selection.some((item) => item.unitId === unitId)
        ? selection.filter((item) => item.unitId !== unitId)
        : [...selection, { unitId, startSeconds: null, endSeconds: null }]
      set({ selection: next })
      schedulePreflight(slug)
    },

    removeSelection: (slug, unitId) => {
      set({ selection: get().selection.filter((item) => item.unitId !== unitId) })
      schedulePreflight(slug)
    },

    clearSelection: (slug) => {
      set({ selection: [], preflight: null })
      cancelPreflight()
      void slug
    },

    moveSelection: (slug, unitId, direction) => {
      const selection = [...get().selection]
      const index = selection.findIndex((item) => item.unitId === unitId)
      const target = index + direction
      if (index < 0 || target < 0 || target >= selection.length) return
      const moved = selection[index]
      const displaced = selection[target]
      if (!moved || !displaced) return
      selection[index] = displaced
      selection[target] = moved
      set({ selection })
      schedulePreflight(slug)
    },

    setTrim: (slug, unitId, edge, value) => {
      set({
        selection: get().selection.map((item) =>
          item.unitId === unitId
            ? { ...item, [edge === 'start' ? 'startSeconds' : 'endSeconds']: value }
            : item,
        ),
      })
      schedulePreflight(slug)
    },

    setCaptionMode: (slug, mode) => {
      set({ captionMode: mode })
      schedulePreflight(slug)
    },

    setCaptionPreset: (slug, preset) => {
      set({ captionPreset: preset })
      if (get().captionMode === 'shorts-native') schedulePreflight(slug)
    },

    refreshPreflight: async (slug) => {
      const { selectedRenderId, selection, captionMode, captionPreset } = get()
      if (!selectedRenderId || selection.length === 0) {
        set({ preflight: null })
        return
      }
      try {
        const preflight = await api.shortsPreflight(
          slug,
          requestFor(selectedRenderId, selection, { mode: captionMode, preset: captionPreset }),
        )
        set({ preflight, error: null })
      } catch (err) {
        set({ error: describeError(err) })
      }
    },

    start: async (slug) => {
      const { selectedRenderId, selection, captionMode, captionPreset } = get()
      if (!selectedRenderId || selection.length === 0) return
      set({ busy: true, error: null, event: null })
      try {
        const job = await api.createShort(
          slug,
          requestFor(selectedRenderId, selection, { mode: captionMode, preset: captionPreset }),
        )
        set({ job })
        if (TERMINAL.has(job.status)) {
          // A cache hit finishes before it starts; just refresh the history.
          await get().loadHistory(slug)
        } else {
          get().attach(slug, job.id)
        }
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
        set({ job: await api.cancelShortJob(job.id) })
      } catch (err) {
        set({ error: describeError(err) })
      }
    },

    retry: async (jobId) => {
      set({ busy: true, error: null })
      try {
        const job = await api.retryShortJob(jobId)
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
      stream = attachJobStream<ShortJobEvent, ShortJob>({
        url: `/api/short-jobs/${jobId}/events`,
        poll: () => api.getShortJob(jobId),
        onEvent: (event) => set({ event }),
        onPoll: (job) => set({ job }),
        isTerminalEvent: (event) => TERMINAL.has(event.status),
        isTerminalJob: (job) => TERMINAL.has(job.status),
        onFinish: () => {
          void (async () => {
            try {
              const job = await api.getShortJob(jobId)
              set({ job })
              await get().loadHistory(slug)
              await get().refreshPreflight(slug)
            } catch (err) {
              set({ error: describeError(err) })
            }
          })()
        },
      })
    },

    detach: () => {
      stopStream()
      cancelPreflight()
      set({ event: null })
    },

    /** After a reload, pick up a Short that is still being built. */
    reattachIfRunning: async (slug) => {
      try {
        const active = await api.activeShortJob(slug)
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
        set({ history: await api.listShorts(slug) })
      } catch (err) {
        set({ error: describeError(err) })
      }
    },

    remove: async (slug, shortId) => {
      try {
        await api.deleteShort(slug, shortId)
        set({ history: get().history.filter((entry) => entry.shortId !== shortId) })
        await get().refreshPreflight(slug)
      } catch (err) {
        set({ error: describeError(err) })
      }
    },
  }
})
