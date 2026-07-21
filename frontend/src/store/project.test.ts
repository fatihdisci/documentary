import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useProjectStore, flushPendingSave } from './project'
import { api } from '@/api/client'
import { ApiError } from '@/api/client'
import type { Project } from '@/api/project-types'

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    schemaVersion: 1,
    projectId: 'abc123',
    name: 'The Dodo',
    slug: 'the-dodo',
    animal: { commonName: 'Dodo', scientificName: 'Raphus cucullatus' },
    metadata: { videoTitle: '', description: '', thumbnailText: '', thumbnailPrompt: '', tags: [] },
    video: {} as Project['video'],
    style: {} as Project['style'],
    audio: {} as Project['audio'],
    music: {} as Project['music'],
    subtitles: {} as Project['subtitles'],
    export: {} as Project['export'],
    intro: {} as Project['intro'],
    outro: {} as Project['outro'],
    scenes: [
      { id: 's1', order: 0, title: 'One', narration: 'First.' } as Project['scenes'][number],
      { id: 's2', order: 1, title: 'Two', narration: 'Second.' } as Project['scenes'][number],
    ],
    pronunciation: {},
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function reset() {
  useProjectStore.setState({
    projects: [],
    project: null,
    images: [],
    selectedSceneId: null,
    loading: false,
    saveStatus: 'idle',
    lastSavedAt: null,
    error: null,
  })
}

beforeEach(() => {
  reset()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('project store', () => {
  it('opens a project and selects the first scene', async () => {
    const project = makeProject()
    vi.spyOn(api, 'getProject').mockResolvedValue({ project, images: [] })

    await useProjectStore.getState().openProject('the-dodo')

    expect(useProjectStore.getState().project?.name).toBe('The Dodo')
    expect(useProjectStore.getState().selectedSceneId).toBe('s1')
  })

  it('surfaces a load failure instead of leaving a half-open project', async () => {
    vi.spyOn(api, 'getProject').mockRejectedValue(
      new ApiError(404, {
        code: 'project_not_found',
        message: 'No project named "ghost" was found.',
        details: null,
        suggestion: 'Reload the project list.',
        logPath: null,
        context: {},
      }),
    )

    await useProjectStore.getState().openProject('ghost')

    const state = useProjectStore.getState()
    expect(state.project).toBeNull()
    expect(state.error?.code).toBe('project_not_found')
    expect(state.error?.suggestion).toBeTruthy()
  })

  it('marks edits dirty immediately and autosaves after a debounce', async () => {
    const project = makeProject()
    vi.spyOn(api, 'getProject').mockResolvedValue({ project, images: [] })
    const save = vi
      .spyOn(api, 'saveProject')
      .mockImplementation(async (_slug, p) => ({ project: p, images: [] }))

    await useProjectStore.getState().openProject('the-dodo')
    useProjectStore.getState().edit((d) => {
      d.metadata.videoTitle = 'New Title'
    })

    // The edit is visible at once; the network call has not happened yet.
    expect(useProjectStore.getState().project?.metadata.videoTitle).toBe('New Title')
    expect(useProjectStore.getState().saveStatus).toBe('dirty')
    expect(save).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1000)

    expect(save).toHaveBeenCalledTimes(1)
    expect(useProjectStore.getState().saveStatus).toBe('saved')
  })

  it('coalesces rapid edits into a single save', async () => {
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    const save = vi
      .spyOn(api, 'saveProject')
      .mockImplementation(async (_slug, p) => ({ project: p, images: [] }))

    await useProjectStore.getState().openProject('the-dodo')
    for (const title of ['a', 'ab', 'abc', 'abcd']) {
      useProjectStore.getState().edit((d) => {
        d.metadata.videoTitle = title
      })
    }
    await vi.advanceTimersByTimeAsync(1000)

    expect(save).toHaveBeenCalledTimes(1)
    expect(save.mock.calls[0]?.[1].metadata.videoTitle).toBe('abcd')
  })

  it('keeps the edit and reports the error when a save fails', async () => {
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    vi.spyOn(api, 'saveProject').mockRejectedValue(
      new ApiError(500, {
        code: 'permission_denied',
        message: 'Could not write project.json.',
        details: 'EACCES',
        suggestion: 'Check folder permissions.',
        logPath: '/tmp/backend.log',
        context: {},
      }),
    )

    await useProjectStore.getState().openProject('the-dodo')
    useProjectStore.getState().edit((d) => {
      d.metadata.videoTitle = 'Unsaved work'
    })
    await vi.advanceTimersByTimeAsync(1000)

    const state = useProjectStore.getState()
    expect(state.saveStatus).toBe('error')
    // The user's text must still be there so it can be retried, not discarded.
    expect(state.project?.metadata.videoTitle).toBe('Unsaved work')
    expect(state.error?.suggestion).toBe('Check folder permissions.')
  })

  it('flushes a pending save on demand', async () => {
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    const save = vi
      .spyOn(api, 'saveProject')
      .mockImplementation(async (_slug, p) => ({ project: p, images: [] }))

    await useProjectStore.getState().openProject('the-dodo')
    useProjectStore.getState().edit((d) => {
      d.metadata.videoTitle = 'Flush me'
    })

    await flushPendingSave()

    expect(save).toHaveBeenCalledTimes(1)
    // The debounce timer must not fire a second, redundant save.
    await vi.advanceTimersByTimeAsync(2000)
    expect(save).toHaveBeenCalledTimes(1)
  })

  it('edits a single scene without disturbing the others', async () => {
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    vi.spyOn(api, 'saveProject').mockImplementation(async (_slug, p) => ({ project: p, images: [] }))

    await useProjectStore.getState().openProject('the-dodo')
    useProjectStore.getState().updateScene('s2', (scene) => {
      scene.title = 'Renamed'
    })

    const scenes = useProjectStore.getState().project?.scenes ?? []
    expect(scenes[0]?.title).toBe('One')
    expect(scenes[1]?.title).toBe('Renamed')
  })

  it('does not mutate the previous project object in place', async () => {
    const project = makeProject()
    vi.spyOn(api, 'getProject').mockResolvedValue({ project, images: [] })
    vi.spyOn(api, 'saveProject').mockImplementation(async (_slug, p) => ({ project: p, images: [] }))

    await useProjectStore.getState().openProject('the-dodo')
    const before = useProjectStore.getState().project
    useProjectStore.getState().edit((d) => {
      d.metadata.videoTitle = 'Changed'
    })

    expect(before?.metadata.videoTitle).toBe('')
    expect(useProjectStore.getState().project).not.toBe(before)
  })

  it('closing cancels any pending autosave', async () => {
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    const save = vi
      .spyOn(api, 'saveProject')
      .mockImplementation(async (_slug, p) => ({ project: p, images: [] }))

    await useProjectStore.getState().openProject('the-dodo')
    useProjectStore.getState().edit((d) => {
      d.metadata.videoTitle = 'x'
    })
    useProjectStore.getState().closeProject()
    await vi.advanceTimersByTimeAsync(2000)

    expect(save).not.toHaveBeenCalled()
    expect(useProjectStore.getState().project).toBeNull()
  })
})
