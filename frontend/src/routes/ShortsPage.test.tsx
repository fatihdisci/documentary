import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ShortsPage } from './ShortsPage'
import { api, ApiError } from '@/api/client'
import { useShortsStore } from '@/store/shorts'
import type {
  ShortJob,
  ShortRecord,
  ShortSourceRender,
  ShortSourceTimeline,
  ShortTimelineSection,
  ShortsPreflightResponse,
} from '@/api/shorts-types'
import { makeProject, seedProject, apiError } from '@/test/factories'

function source(overrides: Partial<ShortSourceRender> = {}): ShortSourceRender {
  return {
    renderId: 'render0001',
    projectSlug: 'the-dodo',
    filename: 'the-dodo_v01.mp4',
    url: '/api/projects/the-dodo/exports/the-dodo_v01.mp4',
    createdAt: '2026-07-01T10:00:00Z',
    durationSeconds: 51,
    width: 1920,
    height: 1080,
    fps: 60,
    quality: 'youtube-hq',
    sizeBytes: 12_582_912,
    sectionCount: 6,
    hasAudio: true,
    thumbnailUrl: '/api/projects/the-dodo/shorts/sources/render0001/poster',
    status: 'completed',
    usable: true,
    issue: null,
    ...overrides,
  }
}

function section(
  unitId: string,
  number: number,
  kind: ShortTimelineSection['kind'],
  start: number,
  duration: number,
  { incoming = 0.5, outgoing = 0.5 } = {},
): ShortTimelineSection {
  return {
    unitId,
    kind,
    number,
    title: unitId === 'intro' ? 'Intro' : unitId === 'outro' ? 'Outro' : `Scene ${number}`,
    startSeconds: start,
    endSeconds: start + duration,
    durationSeconds: duration,
    safeStartSeconds: start + incoming,
    safeEndSeconds: start + duration - outgoing,
    safeDurationSeconds: duration - incoming - outgoing,
    transitionToNext: outgoing ? 'documentary-dissolve' : 'none',
    transitionDurationSeconds: outgoing,
    transitionFromPreviousSeconds: incoming,
    fadeInSeconds: 0,
  }
}

function timeline(overrides: Partial<ShortSourceTimeline> = {}): ShortSourceTimeline {
  return {
    source: source(),
    fps: 60,
    totalDurationSeconds: 51,
    sections: [
      section('intro', 0, 'intro', 0, 6, { incoming: 0 }),
      section('scene-1', 1, 'scene', 5.5, 10),
      section('scene-2', 2, 'scene', 15, 10),
      section('scene-3', 3, 'scene', 24.5, 10),
      section('outro', 4, 'outro', 34, 5, { outgoing: 0 }),
    ],
    minClipSeconds: 0.5,
    recommendedMinSeconds: 25,
    recommendedMaxSeconds: 50,
    warnSeconds: 60,
    maxSeconds: 180,
    ...overrides,
  }
}

function preflight(overrides: Partial<ShortsPreflightResponse> = {}): ShortsPreflightResponse {
  return {
    ready: true,
    blockingIssues: [],
    warnings: [],
    source: source(),
    plan: {
      segments: [],
      groups: [],
      totalDurationSeconds: 18,
      cacheKey: 'cafebabe12345678',
      warnings: [],
    },
    totalDurationSeconds: 18,
    withinRecommendedBand: false,
    exceedsContentIdWarning: false,
    exceedsMaximum: false,
    recommendedMinSeconds: 25,
    recommendedMaxSeconds: 50,
    warnSeconds: 60,
    maxSeconds: 180,
    previewFrames: [
      { groupIndex: 0, timeSeconds: 15.5, url: '/api/projects/the-dodo/shorts/frames/a.jpg' },
    ],
    cachedShortId: null,
    activeJobId: null,
    estimatedRenderSeconds: 12,
    ...overrides,
  }
}

function shortRecord(overrides: Partial<ShortRecord> = {}): ShortRecord {
  return {
    shortId: 'aaaa1111bbbb2222',
    projectSlug: 'the-dodo',
    filename: 'the-dodo-short-aaaa1111bbbb2222.mp4',
    url: '/api/projects/the-dodo/shorts/exports/the-dodo-short-aaaa1111bbbb2222.mp4',
    createdAt: '2026-07-02T09:00:00Z',
    durationSeconds: 32.5,
    sizeBytes: 4_194_304,
    width: 1080,
    height: 1920,
    sourceRenderId: 'render0001',
    sourceVideo: 'the-dodo_v01.mp4',
    cacheKey: 'aaaa1111bbbb2222',
    sectionNumbers: [2, 3],
    sectionTitles: ['Scene 2', 'Scene 3'],
    jobId: 'job1',
    artifacts: [],
    ...overrides,
  }
}

function resetStore() {
  useShortsStore.setState({
    sources: [],
    selectedRenderId: null,
    timeline: null,
    selection: [],
    preflight: null,
    job: null,
    event: null,
    history: [],
    error: null,
    loading: false,
    busy: false,
  })
}

beforeEach(() => {
  resetStore()
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.stubGlobal(
    'EventSource',
    class {
      close() {}
    },
  )
  vi.spyOn(api, 'shortsSources').mockResolvedValue([source()])
  vi.spyOn(api, 'shortsTimeline').mockResolvedValue(timeline())
  vi.spyOn(api, 'shortsPreflight').mockResolvedValue(preflight())
  vi.spyOn(api, 'listShorts').mockResolvedValue([])
  vi.spyOn(api, 'activeShortJob').mockResolvedValue(null)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetStore()
  seedProject(null)
})

async function openPage() {
  seedProject(makeProject())
  render(<ShortsPage />)
  // Wait on the source card specifically: the history table lists the same
  // filename as the Short's source, so a bare text query is ambiguous.
  await screen.findByRole('radio', { name: /the-dodo_v01\.mp4/ })
}

describe('ShortsPage', () => {
  it('prompts to open a project when none is loaded', () => {
    seedProject(null)
    render(<ShortsPage />)
    expect(screen.getByText('Open a project first.')).toBeInTheDocument()
  })

  it('lists completed renders with their details and auto-selects one', async () => {
    await openPage()
    const card = screen.getByRole('radio', { name: /the-dodo_v01\.mp4/ })
    expect(card).toHaveAttribute('aria-checked', 'true')
    expect(card).toHaveTextContent('youtube-hq')
    expect(card).toHaveTextContent('1920×1080 @ 60 fps')
    expect(card).toHaveTextContent('12.0 MB')
  })

  it('explains what to do when there is no completed render', async () => {
    vi.spyOn(api, 'shortsSources').mockResolvedValue([])
    seedProject(makeProject())
    render(<ShortsPage />)
    expect(await screen.findByText(/No completed render yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Render Short' })).toBeDisabled()
  })

  it('marks a stale source as unusable and refuses to select it', async () => {
    vi.spyOn(api, 'shortsSources').mockResolvedValue([
      source({ usable: false, issue: 'The exported video is no longer on disk.' }),
    ])
    seedProject(makeProject())
    render(<ShortsPage />)

    const card = await screen.findByRole('radio', { name: /the-dodo_v01\.mp4/ })
    expect(card).toBeDisabled()
    expect(card).toHaveTextContent('no longer on disk')
  })

  it('numbers the sections intro 0, scenes 1..N, outro N+1', async () => {
    await openPage()
    const cards = await screen.findAllByRole('checkbox')
    expect(cards.map((c) => c.getAttribute('aria-label'))).toEqual([
      'Include section 0, Intro',
      'Include section 1, Scene 1',
      'Include section 2, Scene 2',
      'Include section 3, Scene 3',
      'Include section 4, Outro',
    ])
  })

  it('disables the render button until a section is selected', async () => {
    await openPage()
    expect(screen.getByRole('button', { name: 'Render Short' })).toBeDisabled()

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(screen.getByRole('checkbox', { name: 'Include section 2, Scene 2' }))
    expect(screen.getByRole('button', { name: 'Render Short' })).toBeEnabled()
  })

  it('badges selections with their click order, not their timeline order', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()

    await user.click(screen.getByRole('checkbox', { name: 'Include section 3, Scene 3' }))
    await user.click(screen.getByRole('checkbox', { name: 'Include section 1, Scene 1' }))

    expect(screen.getByLabelText('Position 1 in the Short')).toHaveTextContent('1')
    expect(screen.getByLabelText('Position 2 in the Short')).toHaveTextContent('2')
    // Scene 3 was clicked first, so it holds position 1.
    const sceneThree = screen.getByText('Scene 3').closest('.section-card')!
    expect(within(sceneThree as HTMLElement).getByLabelText(/Position 1/)).toBeInTheDocument()
  })

  it('reorders a selection with the move controls', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()

    await user.click(screen.getByRole('checkbox', { name: 'Include section 1, Scene 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'Include section 3, Scene 3' }))
    expect(useShortsStore.getState().selection.map((s) => s.unitId)).toEqual([
      'scene-1',
      'scene-3',
    ])

    await user.click(screen.getByRole('button', { name: 'Move Scene 3 earlier' }))
    expect(useShortsStore.getState().selection.map((s) => s.unitId)).toEqual([
      'scene-3',
      'scene-1',
    ])
  })

  it('removes a section from the selection', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: 'Include section 2, Scene 2' }))
    await user.click(screen.getByRole('button', { name: 'Remove Scene 2 from the Short' }))
    expect(useShortsStore.getState().selection).toEqual([])
  })

  it('shows trim fields bounded by the section safe range', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: 'Include section 2, Scene 2' }))

    const start = screen.getByLabelText('Start') as HTMLInputElement
    const end = screen.getByLabelText('End') as HTMLInputElement
    // Scene 2 runs 15..25 with a 0.5s dissolve at each end.
    expect(start).toHaveValue(15.5)
    expect(end).toHaveValue(24.5)
    expect(start.min).toBe('15.5')
    expect(start.max).toBe('24.5')
    expect(screen.getByText(/trimmable between 15.50s and 24.50s at 60 fps/)).toBeInTheDocument()
  })

  it('clamps a trim typed outside the safe range', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: 'Include section 2, Scene 2' }))

    const start = screen.getByLabelText('Start')
    await user.clear(start)
    await user.type(start, '5')
    await user.tab()

    await waitFor(() =>
      expect(useShortsStore.getState().selection[0]!.startSeconds).toBeCloseTo(15.5, 3),
    )
  })

  it('totals the selection and reports a single preserved-transition cut', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: 'Include section 2, Scene 2' }))
    await user.click(screen.getByRole('checkbox', { name: 'Include section 3, Scene 3' }))

    // 9 + 9 usable, plus the 0.5s dissolve carried inside the merged cut.
    expect(await screen.findByText('18.5s')).toBeInTheDocument()
    expect(screen.getByText(/total · 1 cut$/)).toBeInTheDocument()
    expect(screen.getByText(/One continuous cut/)).toBeInTheDocument()
  })

  it('says separate cuts join with hard cuts for a non-contiguous selection', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: 'Include section 1, Scene 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'Include section 3, Scene 3' }))

    expect(await screen.findByText(/total · 2 cuts/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing is faded or added/)).toBeInTheDocument()
  })

  it('warns about Content ID past a minute without blocking', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.spyOn(api, 'shortsTimeline').mockResolvedValue(
      timeline({
        sections: [
          section('scene-1', 1, 'scene', 0, 40, { incoming: 0 }),
          section('scene-2', 2, 'scene', 39.5, 40),
          section('scene-3', 3, 'scene', 79, 40, { outgoing: 0 }),
        ],
        totalDurationSeconds: 119,
      }),
    )
    await openPage()

    await user.click(screen.getByRole('checkbox', { name: 'Include section 1, Scene 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'Include section 2, Scene 2' }))

    expect(await screen.findByText(/Content ID claim/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Render Short' })).toBeEnabled()
  })

  it('blocks the render past three minutes', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.spyOn(api, 'shortsTimeline').mockResolvedValue(
      timeline({
        sections: [
          section('scene-1', 1, 'scene', 0, 100, { incoming: 0 }),
          section('scene-2', 2, 'scene', 99.5, 100),
          section('scene-3', 3, 'scene', 199, 100, { outgoing: 0 }),
        ],
        totalDurationSeconds: 299,
      }),
    )
    await openPage()

    await user.click(screen.getByRole('checkbox', { name: 'Include section 1, Scene 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'Include section 2, Scene 2' }))

    expect(await screen.findByText(/Too long to be a Short/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Render Short' })).toBeDisabled()
  })

  it('previews the 9:16 canvas with a real frame from the source', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: 'Include section 2, Scene 2' }))

    await vi.advanceTimersByTimeAsync(500)
    const preview = await screen.findByLabelText('1080×1920 output preview')
    expect(preview).toHaveTextContent('1080 × 1920 · black · centred fit')
    await waitFor(() =>
      expect(screen.getByAltText('First frame of the Short')).toHaveAttribute(
        'src',
        '/api/projects/the-dodo/shorts/frames/a.jpg',
      ),
    )
  })

  it('flags an identical Short that already exists', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.spyOn(api, 'shortsPreflight').mockResolvedValue(
      preflight({ cachedShortId: 'aaaa1111bbbb2222' }),
    )
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: 'Include section 2, Scene 2' }))

    await vi.advanceTimersByTimeAsync(500)
    expect(await screen.findByText(/An identical Short already exists/)).toBeInTheDocument()
  })

  it('sends the selection in click order when rendering', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const createShort = vi
      .spyOn(api, 'createShort')
      .mockResolvedValue({ id: 'job9', status: 'queued', projectSlug: 'the-dodo' } as ShortJob)
    await openPage()

    await user.click(screen.getByRole('checkbox', { name: 'Include section 3, Scene 3' }))
    await user.click(screen.getByRole('checkbox', { name: 'Include section 1, Scene 1' }))
    await user.click(screen.getByRole('button', { name: 'Render Short' }))

    await waitFor(() =>
      expect(createShort).toHaveBeenCalledWith('the-dodo', {
        sourceRenderId: 'render0001',
        segments: [
          { unitId: 'scene-3', startSeconds: null, endSeconds: null },
          { unitId: 'scene-1', startSeconds: null, endSeconds: null },
        ],
      }),
    )
  })

  it('shows live progress and a cancel button while a Short is building', async () => {
    useShortsStore.setState({
      job: { id: 'job9', status: 'running', phase: 'compose', progress: 0.4 } as ShortJob,
      event: {
        jobId: 'job9',
        status: 'running',
        phase: 'compose',
        progress: 0.4,
        message: 'Building the vertical frame — 40%',
        elapsedSeconds: 12,
        estimatedRemainingSeconds: 18,
        errorCode: null,
        errorMessage: null,
        errorSuggestion: null,
      },
    })
    await openPage()

    expect(screen.getByText('Building the vertical frame')).toBeInTheDocument()
    expect(screen.getByText('40%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel Short' })).toBeEnabled()
  })

  it('reports a failure with a retry through the structured ErrorBox', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const retryShortJob = vi
      .spyOn(api, 'retryShortJob')
      .mockResolvedValue({ id: 'job10', status: 'queued', projectSlug: 'the-dodo' } as ShortJob)
    useShortsStore.setState({
      job: {
        id: 'job9',
        status: 'failed',
        phase: 'validate-source',
        progress: 0.1,
        warnings: [],
        artifacts: [],
        errorCode: 'stale_render',
        errorMessage: "'the-dodo_v01.mp4' no longer matches the render it was produced by.",
        errorDetails: 'manifest sha256 abc',
        errorSuggestion: 'Re-render the long video, then build the Short again.',
        logFile: null,
      } as unknown as ShortJob,
    })
    await openPage()

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('stale_render')
    expect(alert).toHaveTextContent('no longer matches the render')
    expect(alert).toHaveTextContent('Re-render the long video')

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(retryShortJob).toHaveBeenCalledWith('job9'))
  })

  it('reports a cache-reused completion without pretending it re-encoded', async () => {
    useShortsStore.setState({
      job: {
        id: 'job9',
        status: 'completed',
        phase: 'publish',
        progress: 1,
        cacheReused: true,
        outputFile: 'the-dodo-short-aaaa1111bbbb2222.mp4',
        totalDurationSeconds: 18.5,
        segmentCount: 2,
        groupCount: 1,
        artifacts: [],
        warnings: [],
      } as unknown as ShortJob,
    })
    await openPage()
    expect(screen.getByText('Reused an identical Short')).toBeInTheDocument()
    expect(screen.getByText(/nothing was re-encoded/)).toBeInTheDocument()
  })

  it('lists finished Shorts with a download link', async () => {
    vi.spyOn(api, 'listShorts').mockResolvedValue([shortRecord()])
    await openPage()

    const row = (await screen.findByText('the-dodo-short-aaaa1111bbbb2222.mp4')).closest('tr')!
    expect(within(row).getByText('2 → 3')).toBeInTheDocument()
    expect(within(row).getByText('the-dodo_v01.mp4')).toBeInTheDocument()
    expect(
      within(row).getByRole('link', { name: /Download the-dodo-short/ }),
    ).toHaveAttribute('href', shortRecord().url)
  })

  it('confirms before deleting, and deletes only that Short', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.spyOn(api, 'listShorts').mockResolvedValue([shortRecord()])
    const deleteShort = vi
      .spyOn(api, 'deleteShort')
      .mockResolvedValue({ shortId: 'aaaa1111bbbb2222', removed: [] })
    await openPage()

    await user.click(await screen.findByRole('button', { name: /Delete the-dodo-short/ }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('The long render it was cut from')

    await user.click(within(dialog).getByRole('button', { name: 'Delete Short' }))
    await waitFor(() =>
      expect(deleteShort).toHaveBeenCalledWith('the-dodo', 'aaaa1111bbbb2222'),
    )
    await waitFor(() =>
      expect(
        screen.queryByText('the-dodo-short-aaaa1111bbbb2222.mp4'),
      ).not.toBeInTheDocument(),
    )
  })

  it('surfaces a backend failure through the structured ErrorBox', async () => {
    vi.spyOn(api, 'shortsSources').mockRejectedValue(
      new ApiError(
        503,
        apiError({
          code: 'ffprobe_not_found',
          message: 'ffprobe could not be found.',
          suggestion: 'Install FFmpeg (brew install ffmpeg).',
        }),
      ),
    )
    seedProject(makeProject())
    render(<ShortsPage />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('ffprobe could not be found.')
    expect(alert).toHaveTextContent('Install FFmpeg (brew install ffmpeg).')
  })

  it('reattaches to a Short that was still running when the page reloaded', async () => {
    const activeShortJob = vi
      .spyOn(api, 'activeShortJob')
      .mockResolvedValue({
        id: 'job-live',
        projectSlug: 'the-dodo',
        status: 'running',
        phase: 'cut-segments',
        progress: 0.2,
      } as ShortJob)
    await openPage()

    await waitFor(() => expect(activeShortJob).toHaveBeenCalledWith('the-dodo'))
    await waitFor(() => expect(useShortsStore.getState().job?.id).toBe('job-live'))
    expect(await screen.findByText('Cutting the selected sections')).toBeInTheDocument()
  })
})
