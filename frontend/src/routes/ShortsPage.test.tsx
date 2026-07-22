import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ShortsPage } from './ShortsPage'
import { api, ApiError } from '@/api/client'
import { useShortsStore } from '@/store/shorts'
import type {
  ShortCaptionSupport,
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

/** A render that prepared a clean master, so it can draw large captions. */
function captionReady(overrides: Partial<ShortCaptionSupport> = {}): ShortCaptionSupport {
  return {
    nativeAvailable: true,
    reason: null,
    sourceHasBurnedInSubtitles: true,
    cueCount: 42,
    cleanMasterFilename: 'the-dodo_v01-clean.mp4',
    cueSidecarFilename: 'the-dodo_v01-clean-shorts-cues.json',
    cueSchemaVersion: 1,
    cleanMasterOrigin: 'dedicated-pass',
    ...overrides,
  }
}

const LEGACY_REASON =
  'This render only has burned-in captions. Re-render the long video with a ' +
  'Shorts-ready clean master to use large Shorts captions.'

function captionLegacy(): ShortCaptionSupport {
  return captionReady({
    nativeAvailable: false,
    reason: LEGACY_REASON,
    cueCount: 0,
    cleanMasterFilename: null,
    cueSidecarFilename: null,
    cueSchemaVersion: null,
    cleanMasterOrigin: null,
  })
}

function resetStore() {
  useShortsStore.setState({
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
    expect(screen.getByText('Önce bir proje açın.')).toBeInTheDocument()
  })

  it('lists completed renders with their details and auto-selects one', async () => {
    await openPage()
    const card = screen.getByRole('radio', { name: /the-dodo_v01\.mp4/ })
    expect(card).toHaveAttribute('aria-checked', 'true')
    expect(card).toHaveTextContent('youtube-hq')
    expect(card).toHaveTextContent('1920×1080 · 60 fps')
    expect(card).toHaveTextContent('12.0 MB')
  })

  it('explains what to do when there is no completed render', async () => {
    vi.spyOn(api, 'shortsSources').mockResolvedValue([])
    seedProject(makeProject())
    render(<ShortsPage />)
    expect(await screen.findByText(/Henüz hazır video yok/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Kısa videoyu oluştur' })).toBeDisabled()
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
      '0. bölümü ekle: Intro',
      '1. bölümü ekle: Scene 1',
      '2. bölümü ekle: Scene 2',
      '3. bölümü ekle: Scene 3',
      '4. bölümü ekle: Outro',
    ])
  })

  it('disables the render button until a section is selected', async () => {
    await openPage()
    expect(screen.getByRole('button', { name: 'Kısa videoyu oluştur' })).toBeDisabled()

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(screen.getByRole('checkbox', { name: '2. bölümü ekle: Scene 2' }))
    expect(screen.getByRole('button', { name: 'Kısa videoyu oluştur' })).toBeEnabled()
  })

  it('badges selections with their click order, not their timeline order', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()

    await user.click(screen.getByRole('checkbox', { name: '3. bölümü ekle: Scene 3' }))
    await user.click(screen.getByRole('checkbox', { name: '1. bölümü ekle: Scene 1' }))

    expect(screen.getByLabelText('Kısa videodaki sırası: 1')).toHaveTextContent('1')
    expect(screen.getByLabelText('Kısa videodaki sırası: 2')).toHaveTextContent('2')
    // Scene 3 was clicked first, so it holds position 1.
    const sceneThree = screen.getByText('Scene 3').closest('.section-card')!
    expect(within(sceneThree as HTMLElement).getByLabelText(/Kısa videodaki sırası: 1/)).toBeInTheDocument()
  })

  it('reorders a selection with the move controls', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()

    await user.click(screen.getByRole('checkbox', { name: '1. bölümü ekle: Scene 1' }))
    await user.click(screen.getByRole('checkbox', { name: '3. bölümü ekle: Scene 3' }))
    expect(useShortsStore.getState().selection.map((s) => s.unitId)).toEqual([
      'scene-1',
      'scene-3',
    ])

    await user.click(screen.getByRole('button', { name: 'Scene 3 bölümünü öne al' }))
    expect(useShortsStore.getState().selection.map((s) => s.unitId)).toEqual([
      'scene-3',
      'scene-1',
    ])
  })

  it('removes a section from the selection', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: '2. bölümü ekle: Scene 2' }))
    await user.click(screen.getByRole('button', { name: 'Scene 2 bölümünü çıkar' }))
    expect(useShortsStore.getState().selection).toEqual([])
  })

  it('shows trim fields bounded by the section safe range', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: '2. bölümü ekle: Scene 2' }))

    const start = screen.getByLabelText('Başlangıç') as HTMLInputElement
    const end = screen.getByLabelText('Bitiş') as HTMLInputElement
    // Scene 2 runs 15..25 with a 0.5s dissolve at each end.
    expect(start).toHaveValue(15.5)
    expect(end).toHaveValue(24.5)
    expect(start.min).toBe('15.5')
    expect(start.max).toBe('24.5')
    expect(screen.getByText(/15.50 sn ile 24.50 sn arasında kırpabilirsiniz/)).toBeInTheDocument()
  })

  it('clamps a trim typed outside the safe range', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: '2. bölümü ekle: Scene 2' }))

    const start = screen.getByLabelText('Başlangıç')
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
    await user.click(screen.getByRole('checkbox', { name: '2. bölümü ekle: Scene 2' }))
    await user.click(screen.getByRole('checkbox', { name: '3. bölümü ekle: Scene 3' }))

    // 9 + 9 usable, plus the 0.5s dissolve carried inside the merged cut.
    expect(await screen.findByText('18.5s')).toBeInTheDocument()
    expect(screen.getByText(/toplam · 1 parça/)).toBeInTheDocument()
    expect(screen.getByText(/Tek parça hâlinde kesiliyor/)).toBeInTheDocument()
  })

  it('says separate cuts join with hard cuts for a non-contiguous selection', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: '1. bölümü ekle: Scene 1' }))
    await user.click(screen.getByRole('checkbox', { name: '3. bölümü ekle: Scene 3' }))

    expect(await screen.findByText(/toplam · 2 parça/)).toBeInTheDocument()
    expect(screen.getByText(/Videoda olmayan hiçbir efekt eklenmez/)).toBeInTheDocument()
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

    await user.click(screen.getByRole('checkbox', { name: '1. bölümü ekle: Scene 1' }))
    await user.click(screen.getByRole('checkbox', { name: '2. bölümü ekle: Scene 2' }))

    expect(await screen.findByText(/telifliyse tüm dünyada engellenebilir/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Kısa videoyu oluştur' })).toBeEnabled()
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

    await user.click(screen.getByRole('checkbox', { name: '1. bölümü ekle: Scene 1' }))
    await user.click(screen.getByRole('checkbox', { name: '2. bölümü ekle: Scene 2' }))

    expect(await screen.findByText(/Kısa video için fazla uzun/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Kısa videoyu oluştur' })).toBeDisabled()
  })

  it('previews the 9:16 canvas with a real frame from the source', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await openPage()
    await user.click(screen.getByRole('checkbox', { name: '2. bölümü ekle: Scene 2' }))

    await vi.advanceTimersByTimeAsync(500)
    const preview = await screen.findByLabelText('1080×1920 çıktı önizlemesi')
    expect(preview).toHaveTextContent('1080 × 1920 · siyah zemin · ortalanmış')
    await waitFor(() =>
      expect(screen.getByAltText('Kısa videonun ilk karesi')).toHaveAttribute(
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
    await user.click(screen.getByRole('checkbox', { name: '2. bölümü ekle: Scene 2' }))

    await vi.advanceTimersByTimeAsync(500)
    expect(await screen.findByText(/Birebir aynısı zaten var/)).toBeInTheDocument()
  })

  it('sends the selection in click order when rendering', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const createShort = vi
      .spyOn(api, 'createShort')
      .mockResolvedValue({ id: 'job9', status: 'queued', projectSlug: 'the-dodo' } as ShortJob)
    await openPage()

    await user.click(screen.getByRole('checkbox', { name: '3. bölümü ekle: Scene 3' }))
    await user.click(screen.getByRole('checkbox', { name: '1. bölümü ekle: Scene 1' }))
    await user.click(screen.getByRole('button', { name: 'Kısa videoyu oluştur' }))

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

    expect(screen.getByText('Dikey görüntü hazırlanıyor')).toBeInTheDocument()
    expect(screen.getByText('40%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'İptal et' })).toBeEnabled()
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

    await user.click(within(alert).getByRole('button', { name: 'Tekrar dene' }))
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
    expect(screen.getByText('Hazır olan kısa video kullanıldı')).toBeInTheDocument()
    expect(screen.getByText(/yeniden işlenmedi/)).toBeInTheDocument()
  })

  it('lists finished Shorts with a download link', async () => {
    vi.spyOn(api, 'listShorts').mockResolvedValue([shortRecord()])
    await openPage()

    const row = (await screen.findByText('the-dodo-short-aaaa1111bbbb2222.mp4')).closest('tr')!
    expect(within(row).getByText('2 → 3')).toBeInTheDocument()
    expect(within(row).getByText('the-dodo_v01.mp4')).toBeInTheDocument()
    expect(
      within(row).getByRole('link', { name: /the-dodo-short.* dosyasını indir/ }),
    ).toHaveAttribute('href', shortRecord().url)
  })

  it('confirms before deleting, and deletes only that Short', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.spyOn(api, 'listShorts').mockResolvedValue([shortRecord()])
    const deleteShort = vi
      .spyOn(api, 'deleteShort')
      .mockResolvedValue({ shortId: 'aaaa1111bbbb2222', removed: [] })
    await openPage()

    await user.click(await screen.findByRole('button', { name: /the-dodo-short.* dosyasını sil/ }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Kesildiği uzun videoya')

    await user.click(within(dialog).getByRole('button', { name: 'Kısa videoyu sil' }))
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
    expect(await screen.findByText('Seçilen bölümler kesiliyor')).toBeInTheDocument()
  })
})

describe('ShortsPage captions', () => {
  const NATIVE = 'Büyük altyazı'
  const LEGACY = 'Videodaki altyazıyı kullan'

  /** A queued job: these tests assert on the request, not the result panel. */
  function queued(id: string): ShortJob {
    return { id, status: 'queued', projectSlug: 'the-dodo' } as ShortJob
  }

  async function openWith(support: ShortCaptionSupport | undefined) {
    const entry = support === undefined ? source() : source({ captions: support })
    vi.spyOn(api, 'shortsSources').mockResolvedValue([entry])
    vi.spyOn(api, 'shortsTimeline').mockResolvedValue(
      timeline({ source: entry }),
    )
    seedProject(makeProject())
    render(<ShortsPage />)
    await screen.findByRole('radio', { name: /the-dodo_v01\.mp4/ })
  }

  async function selectScene(name = '2. bölümü ekle: Scene 2') {
    await userEvent.click(screen.getByRole('checkbox', { name }))
  }

  describe('a legacy, burned-in-only render', () => {
    it('disables large captions and says why', async () => {
      await openWith(captionLegacy())

      const native = screen.getByRole('radio', { name: NATIVE })
      expect(native).toBeDisabled()
      expect(screen.getByText(LEGACY_REASON)).toBeInTheDocument()
    })

    it('keeps the legacy mode available and selected', async () => {
      await openWith(captionLegacy())

      const legacy = screen.getByRole('radio', { name: LEGACY })
      expect(legacy).toBeEnabled()
      expect(legacy).toBeChecked()
    })

    it('does not offer "no captions" without a clean master', async () => {
      await openWith(captionLegacy())
      expect(screen.queryByRole('radio', { name: 'Altyazı olmasın' })).not.toBeInTheDocument()
    })

    it('marks the source card as burned-in only', async () => {
      await openWith(captionLegacy())
      const card = screen.getByRole('radio', { name: /the-dodo_v01\.mp4/ })
      expect(card).toHaveTextContent('Sadece videodaki mevcut altyazı')
    })

    it('still renders a legacy Short from it', async () => {
      const createShort = vi.spyOn(api, 'createShort').mockResolvedValue(queued('job-legacy'))
      await openWith(captionLegacy())
      await selectScene()

      const button = screen.getByRole('button', { name: 'Kısa videoyu oluştur' })
      await waitFor(() => expect(button).toBeEnabled())
      await userEvent.click(button)

      const [, request] = createShort.mock.calls[0]!
      // The legacy request is the old contract exactly: no caption fields.
      expect(request.captionMode).toBeUndefined()
      expect(request.captionStyle).toBeUndefined()
      expect(request.segments).toHaveLength(1)
    })
  })

  describe('a Shorts-ready render', () => {
    it('offers all three caption modes', async () => {
      await openWith(captionReady())

      expect(screen.getByRole('radio', { name: NATIVE })).toBeEnabled()
      expect(screen.getByRole('radio', { name: LEGACY })).toBeEnabled()
      expect(screen.getByRole('radio', { name: 'Altyazı olmasın' })).toBeEnabled()
      expect(screen.getByText('Bu videoda 42 altyazı satırı var')).toBeInTheDocument()
    })

    it('marks the source card as supporting large captions', async () => {
      await openWith(captionReady())
      const card = screen.getByRole('radio', { name: /the-dodo_v01\.mp4/ })
      expect(card).toHaveTextContent('Büyük altyazı kullanılabilir')
    })

    it('reveals the style selector only once large captions are on', async () => {
      await openWith(captionReady())
      expect(screen.queryByRole('radio', { name: 'Normal altyazı boyutu' })).not.toBeInTheDocument()

      await userEvent.click(screen.getByRole('radio', { name: NATIVE }))

      expect(screen.getByRole('radio', { name: 'Normal altyazı boyutu' })).toBeChecked()
      expect(screen.getByRole('radio', { name: 'Büyük altyazı boyutu' })).toBeInTheDocument()
      expect(screen.getByRole('radio', { name: 'Küçük altyazı boyutu' })).toBeInTheDocument()
      expect(
        screen.getByText(/oynatma çubuğu ve beğen\/yorum düğmelerinin üstünü kapatmaz/i),
      ).toBeInTheDocument()
    })

    it('puts the chosen mode and style into the create request', async () => {
      const createShort = vi.spyOn(api, 'createShort').mockResolvedValue(queued('job-native'))
      await openWith(captionReady())
      await selectScene()
      await userEvent.click(screen.getByRole('radio', { name: NATIVE }))
      await userEvent.click(screen.getByRole('radio', { name: 'Büyük altyazı boyutu' }))

      const button = screen.getByRole('button', { name: 'Kısa videoyu oluştur' })
      await waitFor(() => expect(button).toBeEnabled())
      await userEvent.click(button)

      const [, request] = createShort.mock.calls[0]!
      expect(request.captionMode).toBe('shorts-native')
      expect(request.captionStyle).toEqual({ preset: 'large' })
    })

    it('sends the mode but no style when captions are off', async () => {
      const createShort = vi.spyOn(api, 'createShort').mockResolvedValue(queued('job-off'))
      await openWith(captionReady())
      await selectScene()
      await userEvent.click(screen.getByRole('radio', { name: 'Altyazı olmasın' }))

      const button = screen.getByRole('button', { name: 'Kısa videoyu oluştur' })
      await waitFor(() => expect(button).toBeEnabled())
      await userEvent.click(button)

      const [, request] = createShort.mock.calls[0]!
      expect(request.captionMode).toBe('off')
      expect(request.captionStyle).toBeUndefined()
    })

    it('re-runs preflight when the caption style changes', async () => {
      const shortsPreflight = vi.spyOn(api, 'shortsPreflight').mockResolvedValue(
        preflight({ captionMode: 'shorts-native', captionCueCount: 7 }),
      )
      await openWith(captionReady())
      await selectScene()
      await waitFor(() => expect(shortsPreflight).toHaveBeenCalled())

      await userEvent.click(screen.getByRole('radio', { name: NATIVE }))
      await waitFor(() =>
        expect(
          shortsPreflight.mock.calls.some(
            ([, request]) => request.captionMode === 'shorts-native',
          ),
        ).toBe(true),
      )

      await userEvent.click(screen.getByRole('radio', { name: 'Küçük altyazı boyutu' }))
      await waitFor(() =>
        expect(
          shortsPreflight.mock.calls.some(
            ([, request]) => request.captionStyle?.preset === 'compact',
          ),
        ).toBe(true),
      )
    })

    it('shows how many captions the selection actually contains', async () => {
      vi.spyOn(api, 'shortsPreflight').mockResolvedValue(
        preflight({ captionMode: 'shorts-native', captionCueCount: 7 }),
      )
      await openWith(captionReady())
      await selectScene()
      await userEvent.click(screen.getByRole('radio', { name: NATIVE }))

      expect(await screen.findByText(/Seçtiğiniz bölümlerde 7 altyazı satırı var/)).toBeInTheDocument()
    })
  })

  describe('backend rejection', () => {
    it('shows a blocking preflight issue rather than hiding it', async () => {
      vi.spyOn(api, 'shortsPreflight').mockResolvedValue(
        preflight({
          ready: false,
          blockingIssues: [LEGACY_REASON],
          captionMode: 'shorts-native',
        }),
      )
      await openWith(captionReady())
      await selectScene()
      await userEvent.click(screen.getByRole('radio', { name: NATIVE }))

      expect(await screen.findByText(`✕ ${LEGACY_REASON}`)).toBeInTheDocument()
    })

    it('surfaces a rejected create through the ErrorBox', async () => {
      vi.spyOn(api, 'createShort').mockRejectedValue(
        new ApiError(
          422,
          apiError({
            code: 'short_captions_unavailable',
            message: LEGACY_REASON,
            suggestion: "Or switch to 'Use captions already in video'.",
          }),
        ),
      )
      await openWith(captionReady())
      await selectScene()
      await userEvent.click(screen.getByRole('radio', { name: NATIVE }))

      const button = screen.getByRole('button', { name: 'Kısa videoyu oluştur' })
      await waitFor(() => expect(button).toBeEnabled())
      await userEvent.click(button)

      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent(LEGACY_REASON)
    })
  })

  describe('an older backend that sends no caption fields at all', () => {
    it('treats the render as legacy rather than offering something unsupported', async () => {
      await openWith(undefined)

      expect(screen.getByRole('radio', { name: NATIVE })).toBeDisabled()
      expect(screen.getByRole('radio', { name: LEGACY })).toBeChecked()
      expect(screen.queryByRole('radio', { name: 'Altyazı olmasın' })).not.toBeInTheDocument()
    })

    it('still lists, selects, plans and renders exactly as before', async () => {
      const createShort = vi.spyOn(api, 'createShort').mockResolvedValue(queued('job-old'))
      await openWith(undefined)
      await selectScene()

      const button = screen.getByRole('button', { name: 'Kısa videoyu oluştur' })
      await waitFor(() => expect(button).toBeEnabled())
      await userEvent.click(button)

      expect(createShort).toHaveBeenCalledWith(
        'the-dodo',
        expect.objectContaining({ sourceRenderId: 'render0001' }),
      )
    })

    it('renders a history row whose caption mode is missing', async () => {
      vi.spyOn(api, 'listShorts').mockResolvedValue([shortRecord()])
      await openWith(undefined)

      const row = await screen.findByText('the-dodo-short-aaaa1111bbbb2222.mp4')
      expect(row.closest('tr')).toHaveTextContent('Videodaki')
    })
  })
})
