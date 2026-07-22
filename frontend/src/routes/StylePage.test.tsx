import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StylePage } from './StylePage'
import { useProjectStore } from '@/store/project'
import { api } from '@/api/client'
import type { Project, Style, TextStyle, SubtitleStyle } from '@/api/project-types'

function textStyle(overrides: Partial<TextStyle> = {}): TextStyle {
  return {
    fontFamily: 'Inter',
    fontWeight: 700,
    size: 64,
    color: '#FFFFFF',
    letterSpacing: 0,
    lineSpacing: 1.25,
    shadow: true,
    shadowBlur: 12,
    shadowOffset: 3,
    outlineWidth: 0,
    outlineColor: '#000000',
    box: true,
    boxColor: '#000000',
    boxOpacity: 0.45,
    boxPaddingX: 32,
    boxPaddingY: 18,
    boxRadius: 8,
    animation: 'fade',
    fadeInSeconds: 0.5,
    fadeOutSeconds: 0.5,
    maxWidthRatio: 0.62,
    ...overrides,
  }
}

function subtitleStyle(): SubtitleStyle {
  return {
    ...textStyle({ size: 38, fontWeight: 500, maxWidthRatio: 0.8 }),
    maxCharsPerLine: 42,
    maxLines: 2,
    minCueSeconds: 1.2,
    maxCueSeconds: 6.0,
    maxCharsPerSecond: 17.0,
  }
}

function makeStyle(): Style {
  return {
    fontFamily: 'Inter',
    title: textStyle({ size: 64, fontWeight: 700 }),
    subtitle: textStyle({ size: 36, fontWeight: 400 }),
    caption: textStyle({ size: 38, fontWeight: 500 }),
    subtitles: subtitleStyle(),
    textPosition: 'bottom-left',
    textSafeMargin: 80,
    overlayOpacity: 0.45,
    transitionPreset: 'documentary-dissolve',
    watermarkText: '',
    watermarkOpacity: 0.5,
    showScientificName: true,
  }
}

function makeProject(): Project {
  return {
    schemaVersion: 1,
    projectId: 'abc123',
    name: 'The Dodo',
    slug: 'the-dodo',
    animal: { commonName: 'Dodo', scientificName: 'Raphus cucullatus' },
    metadata: { videoTitle: '', description: '', thumbnailText: '', thumbnailPrompt: '', tags: [] },
    video: {} as Project['video'],
    style: makeStyle(),
    audio: {} as Project['audio'],
    music: {} as Project['music'],
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

function seed(project: Project) {
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
  // Edits schedule a debounced autosave; keep it off the network.
  vi.spyOn(api, 'saveProject').mockImplementation(async (_slug, p) => ({ project: p, images: [] }))
})

afterEach(() => {
  vi.restoreAllMocks()
  seed(makeProject())
  useProjectStore.setState({ project: null })
})

describe('StylePage', () => {
  it('prompts to open a project when none is loaded', () => {
    useProjectStore.setState({ project: null })
    render(<StylePage />)
    expect(screen.getByText('Önce bir proje açın.')).toBeInTheDocument()
  })

  it('renders the global controls seeded from the project style', () => {
    seed(makeProject())
    render(<StylePage />)

    expect(screen.getByText('Genel')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Belgesel geçişi (önerilen)' })).toBeInTheDocument()
    // Default tab is Title, whose size is 64.
    expect(screen.getByText('Boyut — 64px')).toBeInTheDocument()
  })

  it('edits the active text class and writes it to the store', () => {
    seed(makeProject())
    render(<StylePage />)

    const sizeSlider = screen.getByLabelText(/Boyut — 64px/)
    fireEvent.change(sizeSlider, { target: { value: '90' } })

    expect(useProjectStore.getState().project?.style.title.size).toBe(90)
  })

  it('switches to the caption class and edits it independently of the title', async () => {
    const user = userEvent.setup()
    seed(makeProject())
    render(<StylePage />)

    await user.click(screen.getByRole('tab', { name: 'Küçük yazı' }))
    // Caption default size is 38.
    expect(screen.getByText('Boyut — 38px')).toBeInTheDocument()

    const colorHex = screen.getAllByDisplayValue('#FFFFFF')[0] as HTMLInputElement
    await user.clear(colorHex)
    await user.type(colorHex, '#FFD700')

    const style = useProjectStore.getState().project?.style
    expect(style?.caption.color).toBe('#FFD700')
    // The title class must be untouched.
    expect(style?.title.color).toBe('#FFFFFF')
  })

  it('reveals subtitle cue-timing controls only on the Subtitles tab', async () => {
    const user = userEvent.setup()
    seed(makeProject())
    render(<StylePage />)

    expect(screen.queryByText(/Altyazı satırları/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Altyazı' }))
    expect(screen.getByText(/Altyazı satırları/)).toBeInTheDocument()
    expect(screen.getByText(/Okuma hızı sınırı — saniyede 17 harf/)).toBeInTheDocument()
  })

  it('changes the default transition preset', () => {
    seed(makeProject())
    render(<StylePage />)

    const transition = screen
      .getAllByRole('combobox')
      .find((el) => (el as HTMLSelectElement).value === 'documentary-dissolve')!
    fireEvent.change(transition, { target: { value: 'blur-dissolve' } })

    expect(useProjectStore.getState().project?.style.transitionPreset).toBe('blur-dissolve')
  })
})
