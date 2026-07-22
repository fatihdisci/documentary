import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MusicPage } from './MusicPage'
import { useProjectStore } from '@/store/project'
import { api } from '@/api/client'
import type { Project } from '@/api/project-types'

function makeProject(): Project {
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
    music: {
      source: 'none',
      file: null,
      loopIfShort: true,
      introLevelDb: -22,
      outroLevelDb: -22,
    },
    subtitles: {} as Project['subtitles'],
    export: {} as Project['export'],
    intro: {} as Project['intro'],
    outro: {} as Project['outro'],
    scenes: [],
    pronunciation: {},
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  }
}

function seed(project: Project | null) {
  useProjectStore.setState({
    projects: [],
    project,
    images: [],
    selectedSceneId: null,
    loading: false,
    saveStatus: 'idle',
    lastSavedAt: null,
    error: null,
  })
}

beforeEach(() => {
  vi.spyOn(api, 'saveProject').mockImplementation(async (_slug, p) => ({ project: p, images: [] }))
  vi.spyOn(api, 'listMusic').mockResolvedValue([
    { filename: 'forest.mp3', sizeBytes: 2_500_000 },
    { filename: 'drone.wav', sizeBytes: 500_000 },
  ])
})

afterEach(() => {
  vi.restoreAllMocks()
  seed(null)
})

describe('MusicPage', () => {
  it('prompts to open a project when none is loaded', () => {
    seed(null)
    render(<MusicPage />)
    expect(screen.getByText('Open a project first.')).toBeInTheDocument()
  })

  it('lists the uploaded tracks with their sizes', async () => {
    seed(makeProject())
    render(<MusicPage />)

    expect(await screen.findByText('forest.mp3')).toBeInTheDocument()
    expect(screen.getByText('drone.wav')).toBeInTheDocument()
    expect(screen.getByText('2.4 MB')).toBeInTheDocument()
  })

  it('exposes a music level control that edits the project and shows the level', async () => {
    const project = makeProject()
    project.audio = { ...project.audio, musicVolumeDb: -30 }
    seed(project)
    render(<MusicPage />)

    await screen.findByText('forest.mp3')
    expect(screen.getByRole('heading', { name: 'Music level' })).toBeInTheDocument()
    expect(screen.getByText('-30 dB')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('spinbutton', { name: 'Music level in decibels' }), {
      target: { value: '-20' },
    })

    expect(useProjectStore.getState().project?.audio.musicVolumeDb).toBe(-20)
    expect(screen.getByText('-20 dB')).toBeInTheDocument()
  })

  it('selecting a track points the project at it as uploaded music', async () => {
    const user = userEvent.setup()
    seed(makeProject())
    render(<MusicPage />)

    const row = (await screen.findByText('forest.mp3')).closest('tr')!
    await user.click(within(row).getByRole('button', { name: 'Use' }))

    const music = useProjectStore.getState().project?.music
    expect(music?.source).toBe('uploaded')
    expect(music?.file).toBe('forest.mp3')
    // The selected row is flagged in use.
    expect(await screen.findByText(/in use/)).toBeInTheDocument()
  })

  it('“No music” clears the source and file', async () => {
    const user = userEvent.setup()
    const project = makeProject()
    project.music = { ...project.music, source: 'uploaded', file: 'forest.mp3' }
    seed(project)
    render(<MusicPage />)

    await screen.findByText('forest.mp3')
    await user.click(screen.getByRole('button', { name: 'No music' }))

    const music = useProjectStore.getState().project?.music
    expect(music?.source).toBe('none')
    expect(music?.file).toBeNull()
  })

  it('deleting a track asks for confirmation, then calls the API and refreshes', async () => {
    const user = userEvent.setup()
    const del = vi.spyOn(api, 'deleteMusic').mockResolvedValue(undefined)
    seed(makeProject())
    render(<MusicPage />)

    const row = (await screen.findByText('drone.wav')).closest('tr')!
    await user.click(within(row).getByRole('button', { name: 'Delete' }))

    // A confirmation modal, not an immediate destructive action.
    const dialog = screen.getByRole('dialog', { name: 'Delete this track?' })
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(del).toHaveBeenCalledWith('the-dodo', 'drone.wav'))
  })

  it('uploads a chosen file and makes it the active track', async () => {
    const user = userEvent.setup()
    const up = vi.spyOn(api, 'uploadMusic').mockResolvedValue({ filename: 'new-bed.mp3' })
    seed(makeProject())
    render(<MusicPage />)

    await screen.findByText('forest.mp3')
    const input = screen.getByLabelText('Upload music track')
    const file = new File([new Uint8Array([1, 2, 3])], 'new-bed.mp3', { type: 'audio/mpeg' })
    await user.upload(input, file)

    await waitFor(() => expect(up).toHaveBeenCalled())
    expect(useProjectStore.getState().project?.music.file).toBe('new-bed.mp3')
  })
})
