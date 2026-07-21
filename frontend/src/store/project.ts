/**
 * Project editing store.
 *
 * Edits are applied to local state immediately and flushed to the backend on a
 * debounce, so navigating between sections never loses work. The save status is
 * always visible in the top bar, and a failed save keeps the dirty flag set so
 * the change is retried rather than silently dropped.
 */

import { create } from 'zustand'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { ImageInfo, Project, ProjectSummary, Scene } from '@/api/project-types'

export type SaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'error'

const AUTOSAVE_DELAY_MS = 900

interface ProjectState {
  projects: ProjectSummary[]
  project: Project | null
  images: ImageInfo[]
  selectedSceneId: string | null

  loading: boolean
  saveStatus: SaveStatus
  lastSavedAt: number | null
  error: ApiErrorPayload | null

  loadProjects: () => Promise<void>
  openProject: (slug: string) => Promise<void>
  closeProject: () => void
  createProject: (name: string) => Promise<string | null>

  /** Apply a local edit and schedule an autosave. */
  edit: (mutate: (draft: Project) => void) => void
  save: () => Promise<void>
  reloadImages: () => Promise<void>

  selectScene: (sceneId: string | null) => void
  updateScene: (sceneId: string, mutate: (draft: Scene) => void) => void

  clearError: () => void
  setError: (error: unknown) => void
}

let saveTimer: ReturnType<typeof setTimeout> | null = null

/** Structured clone that works in jsdom and browsers alike. */
function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  projects: [],
  project: null,
  images: [],
  selectedSceneId: null,
  loading: false,
  saveStatus: 'idle',
  lastSavedAt: null,
  error: null,

  clearError: () => set({ error: null }),
  setError: (error) => set({ error: describeError(error) }),

  loadProjects: async () => {
    set({ loading: true })
    try {
      set({ projects: await api.listProjects(), error: null })
    } catch (err) {
      set({ error: describeError(err) })
    } finally {
      set({ loading: false })
    }
  },

  openProject: async (slug) => {
    set({ loading: true, error: null })
    try {
      const response = await api.getProject(slug)
      set({
        project: response.project,
        images: response.images,
        selectedSceneId: response.project.scenes[0]?.id ?? null,
        saveStatus: 'idle',
      })
    } catch (err) {
      set({ error: describeError(err), project: null })
    } finally {
      set({ loading: false })
    }
  },

  closeProject: () => {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = null
    set({ project: null, images: [], selectedSceneId: null, saveStatus: 'idle' })
  },

  createProject: async (name) => {
    try {
      const response = await api.createProject(name)
      set({ project: response.project, images: [], selectedSceneId: null, saveStatus: 'idle' })
      await get().loadProjects()
      return response.project.slug
    } catch (err) {
      set({ error: describeError(err) })
      return null
    }
  },

  edit: (mutate) => {
    const current = get().project
    if (!current) return
    const draft = clone(current)
    mutate(draft)
    set({ project: draft, saveStatus: 'dirty' })

    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      void get().save()
    }, AUTOSAVE_DELAY_MS)
  },

  save: async () => {
    const { project } = get()
    if (!project) return
    if (saveTimer) {
      clearTimeout(saveTimer)
      saveTimer = null
    }
    set({ saveStatus: 'saving' })
    try {
      const response = await api.saveProject(project.slug, project)
      set({
        project: response.project,
        images: response.images,
        saveStatus: 'saved',
        lastSavedAt: Date.now(),
        error: null,
      })
    } catch (err) {
      // Stay dirty: the edit is still only in memory and must be retried.
      set({ saveStatus: 'error', error: describeError(err) })
    }
  },

  reloadImages: async () => {
    const { project } = get()
    if (!project) return
    try {
      set({ images: await api.listImages(project.slug) })
    } catch (err) {
      set({ error: describeError(err) })
    }
  },

  selectScene: (sceneId) => set({ selectedSceneId: sceneId }),

  updateScene: (sceneId, mutate) => {
    get().edit((draft) => {
      const scene = draft.scenes.find((s) => s.id === sceneId)
      if (scene) mutate(scene)
    })
  },
}))

/** Flush any pending autosave. Call before navigating away or rendering. */
export async function flushPendingSave(): Promise<void> {
  const { saveStatus, save } = useProjectStore.getState()
  if (saveStatus === 'dirty' || saveStatus === 'error') await save()
}
