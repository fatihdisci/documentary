import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PublishingPage } from './PublishingPage'
import { api } from '@/api/client'
import { usePublishingStore } from '@/store/publishing'
import type {
  DraftResponse,
  MediaItem,
  PublishDraft,
  PublishHistoryEntry,
  PublishJob,
  YouTubeConnection,
} from '@/api/publishing-types'
import { makeProject, seedProject } from '@/test/factories'

// --- fixtures ---------------------------------------------------------------

function longMedia(overrides: Partial<MediaItem> = {}): MediaItem {
  return {
    mediaId: 'long:render0001',
    kind: 'long',
    filename: 'the-dodo_v01.mp4',
    url: '/api/projects/the-dodo/exports/the-dodo_v01.mp4',
    projectSlug: 'the-dodo',
    projectName: 'The Dodo',
    createdAt: '2026-07-01T10:00:00Z',
    durationSeconds: 512,
    sizeBytes: 268_435_456,
    width: 1920,
    height: 1080,
    fps: 60,
    quality: 'youtube-hq',
    thumbnailUrl: null,
    recommended: true,
    note: null,
    fingerprint: { filename: 'the-dodo_v01.mp4', sizeBytes: 268_435_456, sha256: 'a'.repeat(64) },
    captionFilename: 'the-dodo_v01.srt',
    captionUrl: '/api/projects/the-dodo/exports/the-dodo_v01.srt',
    hasDraft: false,
    publishedVideoId: null,
    ...overrides,
  }
}

function shortMedia(overrides: Partial<MediaItem> = {}): MediaItem {
  return longMedia({
    mediaId: 'short:aaaa1111',
    kind: 'short',
    filename: 'the-dodo-short-aaaa1111.mp4',
    url: '/api/projects/the-dodo/shorts/exports/the-dodo-short-aaaa1111.mp4',
    durationSeconds: 38,
    sizeBytes: 8_388_608,
    width: 1080,
    height: 1920,
    quality: 'short',
    captionFilename: null,
    captionUrl: null,
    fingerprint: {
      filename: 'the-dodo-short-aaaa1111.mp4',
      sizeBytes: 8_388_608,
      sha256: 'b'.repeat(64),
    },
    ...overrides,
  })
}

function draft(overrides: Partial<PublishDraft> = {}): PublishDraft {
  return {
    mediaId: 'long:render0001',
    projectSlug: 'the-dodo',
    sourceFingerprint: longMedia().fingerprint,
    common: {
      title: 'The Dodo: A Bird That Never Learned to Run',
      description: 'A documentary about the dodo.',
      tags: ['dodo', 'extinct animals'],
      thumbnailText: 'GONE IN 80 YEARS',
      thumbnailPrompt: 'A dodo on a beach at dusk',
    },
    youtube: {
      title: 'The Dodo: A Bird That Never Learned to Run',
      description: 'A documentary about the dodo.',
      tags: ['dodo', 'extinct animals'],
      categoryId: '27',
      defaultLanguage: 'en',
      defaultAudioLanguage: 'en',
      privacyStatus: 'private',
      publishMode: 'now',
      publishAtLocal: null,
      madeForKids: false,
      notifySubscribers: true,
      embeddable: true,
      thumbnailFile: null,
      captionFile: 'the-dodo_v01.srt',
      captionSource: 'export',
      captionLanguage: 'en',
      captionName: 'English',
      captionIsDraft: false,
      uploadCaptions: true,
    },
    instagram: { caption: '', hashtags: [], account: '', publishMode: 'now', publishAtLocal: null },
    facebook: { caption: '', hashtags: [], account: '', publishMode: 'now', publishAtLocal: null },
    tiktok: {
      caption: '',
      hashtags: [],
      account: '',
      publishMode: 'now',
      publishAtLocal: null,
      privacy: 'private',
      allowComments: true,
      allowDuet: false,
      allowStitch: false,
    },
    updatedAt: '2026-07-02T09:00:00Z',
    ...overrides,
  }
}

function draftResponse(overrides: Partial<DraftResponse> = {}): DraftResponse {
  return {
    draft: draft(),
    media: longMedia(),
    sourceChanged: false,
    sourceChangedReason: null,
    duplicateOf: null,
    ...overrides,
  }
}

function connection(overrides: Partial<YouTubeConnection> = {}): YouTubeConnection {
  return {
    clientFilePresent: true,
    clientFileName: 'client_secret_test.json',
    availableClientFiles: ['client_secret_test.json'],
    tokenPresent: true,
    connected: true,
    needsReconnect: false,
    expired: false,
    scopesSufficient: true,
    missingScopes: [],
    channelId: 'UC_test_channel',
    channelTitle: 'Vanished Earth',
    channelThumbnailUrl: null,
    checkedAt: '2026-07-28T09:00:00Z',
    statusMessage: 'Bağlantı geçerli.',
    problem: null,
    suggestion: null,
    ...overrides,
  }
}

function job(overrides: Partial<PublishJob> = {}): PublishJob {
  return {
    id: 'pubjob01',
    projectSlug: 'the-dodo',
    mediaId: 'long:render0001',
    platform: 'youtube',
    source: longMedia().fingerprint,
    status: 'running',
    phase: 'upload-video',
    progress: 0.42,
    message: 'Video yükleniyor — 100 / 256 MB',
    createdAt: '2026-07-28T09:00:00Z',
    startedAt: '2026-07-28T09:00:01Z',
    finishedAt: null,
    videoId: null,
    videoUrl: null,
    title: 'The Dodo',
    requestedPrivacyStatus: 'private',
    requestedPublishAt: null,
    actualPrivacyStatus: null,
    actualPublishAt: null,
    uploadStatus: null,
    processingStatus: null,
    thumbnailStatus: 'skipped',
    thumbnailError: null,
    captionStatus: 'skipped',
    captionError: null,
    captionTrackId: null,
    uploadedBytes: 104_857_600,
    totalBytes: 268_435_456,
    warnings: [],
    errorCode: null,
    errorMessage: null,
    errorDetails: null,
    errorSuggestion: null,
    ...overrides,
  }
}

function historyEntry(overrides: Partial<PublishHistoryEntry> = {}): PublishHistoryEntry {
  return {
    entryId: 'entry01',
    jobId: 'pubjob01',
    projectSlug: 'the-dodo',
    mediaId: 'long:render0001',
    platform: 'youtube',
    filename: 'the-dodo_v01.mp4',
    title: 'The Dodo',
    videoId: 'vid_0001',
    videoUrl: 'https://youtu.be/vid_0001',
    uploadedAt: '2026-07-20T18:00:00Z',
    requestedPublishAt: null,
    actualPublishAt: null,
    privacyStatus: 'private',
    uploadStatus: 'uploaded',
    processingStatus: 'succeeded',
    thumbnailStatus: 'uploaded',
    captionStatus: 'uploaded',
    source: longMedia().fingerprint,
    warnings: [],
    ...overrides,
  }
}

function resetStore() {
  usePublishingStore.setState({
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
  })
}

beforeEach(() => {
  resetStore()
  seedProject(
    makeProject({
      metadata: {
        videoTitle: 'The Dodo: A Bird That Never Learned to Run',
        description: 'A documentary about the dodo.',
        thumbnailText: 'GONE IN 80 YEARS',
        thumbnailPrompt: 'A dodo on a beach at dusk',
        tags: ['dodo', 'extinct animals'],
      },
    }),
  )
  vi.spyOn(api, 'youtubeStatus').mockResolvedValue(connection())
  vi.spyOn(api, 'publishingMedia').mockResolvedValue([longMedia(), shortMedia()])
  vi.spyOn(api, 'getPublishDraft').mockResolvedValue(draftResponse())
  vi.spyOn(api, 'savePublishDraft').mockImplementation(async (_slug, mediaId, value) =>
    draftResponse({ draft: { ...value, mediaId } }),
  )
  vi.spyOn(api, 'publishHistory').mockResolvedValue([])
  vi.spyOn(api, 'activePublishJob').mockResolvedValue(null)
})

afterEach(() => {
  vi.restoreAllMocks()
})

/** The YouTube title field, told apart from TikTok's "Başlık / açıklama". */
function titleInput(): HTMLInputElement {
  return screen.getByLabelText(/Başlık/, { selector: '#publish-title' }) as HTMLInputElement
}

function descriptionInput(): HTMLTextAreaElement {
  return screen.getByLabelText(/Açıklama/, {
    selector: '#publish-description',
  }) as HTMLTextAreaElement
}

async function renderPage() {
  render(<PublishingPage />)
  // The filename also appears in the history table, so match all of them.
  await waitFor(() => expect(screen.getAllByText('the-dodo_v01.mp4').length).toBeGreaterThan(0))
}

// --- tests ------------------------------------------------------------------

describe('media selection', () => {
  it('lists both long videos and Shorts with their details', async () => {
    await renderPage()

    expect(screen.getAllByText('the-dodo_v01.mp4').length).toBeGreaterThan(0)
    expect(screen.getByText('the-dodo-short-aaaa1111.mp4')).toBeInTheDocument()
    expect(screen.getAllByText('Uzun video').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Kısa video').length).toBeGreaterThan(0)
    // Size, duration and resolution are all on the card.
    expect(screen.getByText(/8 dk 32 sn/)).toBeInTheDocument()
    expect(screen.getByText(/1920×1080/)).toBeInTheDocument()
    expect(screen.getByText(/1080×1920/)).toBeInTheDocument()
  })

  it('loads that file’s draft when another card is picked', async () => {
    const user = userEvent.setup()
    await renderPage()

    const shortDraft = draftResponse({
      draft: draft({ mediaId: 'short:aaaa1111', youtube: { ...draft().youtube, title: 'Short title' } }),
      media: shortMedia(),
    })
    vi.mocked(api.getPublishDraft).mockResolvedValue(shortDraft)

    await user.click(screen.getByText('the-dodo-short-aaaa1111.mp4'))

    await waitFor(() =>
      expect(api.getPublishDraft).toHaveBeenCalledWith('the-dodo', 'short:aaaa1111'),
    )
    await waitFor(() =>
      expect(titleInput()).toHaveValue('Short title'),
    )
  })

  it('hides files that are not worth publishing until asked', async () => {
    vi.mocked(api.publishingMedia).mockResolvedValue([
      longMedia(),
      shortMedia({
        mediaId: 'long:preview1',
        kind: 'long',
        filename: 'the-dodo_preview.mp4',
        recommended: false,
        note: 'Hızlı deneme kalitesinde oluşturulmuş.',
      }),
    ])
    const user = userEvent.setup()
    await renderPage()

    expect(screen.queryByText('the-dodo_preview.mp4')).not.toBeInTheDocument()

    await user.click(screen.getByLabelText(/Yayına uygun olmayanları da göster/))
    expect(screen.getByText('the-dodo_preview.mp4')).toBeInTheDocument()
    expect(screen.getByText(/Hızlı deneme kalitesinde/)).toBeInTheDocument()
  })
})

describe('metadata editing', () => {
  it('fills the fields from the project metadata', async () => {
    await renderPage()

    expect(titleInput()).toHaveValue(
      'The Dodo: A Bird That Never Learned to Run',
    )
    expect(descriptionInput()).toHaveValue('A documentary about the dodo.')
    expect(screen.getByText('dodo')).toBeInTheDocument()
    expect(screen.getByText('extinct animals')).toBeInTheDocument()
    expect(screen.getByLabelText('Kategori')).toHaveValue('27')
    expect(screen.getByLabelText('Varsayılan metadata dili')).toHaveValue('en')
    expect(screen.getByLabelText('Varsayılan ses dili')).toHaveValue('en')
  })

  it('lets every field be changed by hand', async () => {
    const user = userEvent.setup()
    await renderPage()

    const title = titleInput()
    await user.clear(title)
    await user.type(title, 'Elle yazılmış başlık')
    expect(title).toHaveValue('Elle yazılmış başlık')

    await user.selectOptions(screen.getByLabelText('Kategori'), '28')
    expect(screen.getByLabelText('Kategori')).toHaveValue('28')

    await user.click(screen.getByLabelText(/Abonelere bildirim gönder/))
    expect(screen.getByLabelText(/Abonelere bildirim gönder/)).not.toBeChecked()
  })

  it('adds and removes tags', async () => {
    const user = userEvent.setup()
    await renderPage()

    const tagInput = screen.getByLabelText('Etiketler')
    await user.type(tagInput, 'natural history{Enter}')
    expect(screen.getByText('natural history')).toBeInTheDocument()

    await user.click(screen.getByLabelText('dodo etiketini kaldır'))
    expect(screen.queryByText('dodo')).not.toBeInTheDocument()
  })

  it('refills from the project metadata on demand', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'getProject').mockResolvedValue({
      project: makeProject({
        metadata: {
          videoTitle: 'Projeden gelen başlık',
          description: 'Projeden gelen açıklama',
          thumbnailText: '',
          thumbnailPrompt: '',
          tags: ['yeni etiket'],
        },
      }),
      images: [],
    })
    await renderPage()

    const title = titleInput()
    await user.clear(title)
    await user.type(title, 'Geçici bir başlık')

    await user.click(screen.getByRole('button', { name: 'Proje metadatasından tekrar doldur' }))

    await waitFor(() =>
      expect(titleInput()).toHaveValue('Projeden gelen başlık'),
    )
    expect(screen.getByText('yeni etiket')).toBeInTheDocument()
  })

  it('shows live counters and flags a title over the limit', async () => {
    const user = userEvent.setup()
    await renderPage()

    expect(screen.getByText(/42 \/ 100 karakter/)).toBeInTheDocument()
    expect(screen.getByText(/29 \/ 5000 bayt/)).toBeInTheDocument()

    const title = titleInput()
    await user.clear(title)
    await user.type(title, 'x'.repeat(101))

    expect(screen.getByText(/101 \/ 100 karakter/)).toBeInTheDocument()
    expect(title).toHaveAttribute('aria-invalid', 'true')
  })

  it('shows the date field only when scheduling is chosen', async () => {
    const user = userEvent.setup()
    await renderPage()

    expect(screen.queryByLabelText(/Planlanan tarih ve saat/)).not.toBeInTheDocument()

    await user.click(screen.getAllByLabelText('İleri tarihe planla')[0]!)

    expect(screen.getByLabelText(/Planlanan tarih ve saat/)).toBeInTheDocument()
    expect(screen.getByText(/İstanbul saatiyle/)).toBeInTheDocument()
  })

  it('keeps the thumbnail notes out of what gets uploaded', async () => {
    await renderPage()

    expect(screen.getByText(/Thumbnail notları \(YouTube'a gönderilmez\)/)).toBeInTheDocument()
  })
})

describe('drafts are per file', () => {
  it('saves the edited draft against the selected media id', async () => {
    const user = userEvent.setup()
    await renderPage()

    const title = titleInput()
    await user.clear(title)
    await user.type(title, 'Yeni başlık')

    await waitFor(
      () => expect(api.savePublishDraft).toHaveBeenCalled(),
      { timeout: 3000 },
    )
    const [slug, mediaId, saved] = vi.mocked(api.savePublishDraft).mock.calls.at(-1)!
    expect(slug).toBe('the-dodo')
    expect(mediaId).toBe('long:render0001')
    expect(saved.youtube.title).toBe('Yeni başlık')
  })
})

describe('the YouTube card', () => {
  it('disables uploading while the account is not connected', async () => {
    vi.mocked(api.youtubeStatus).mockResolvedValue(
      connection({
        connected: false,
        scopesSufficient: false,
        tokenPresent: false,
        statusMessage: 'İstemci dosyası hazır, hesap henüz bağlanmadı.',
        suggestion: "“YouTube'a bağlan” düğmesine basın.",
      }),
    )
    await renderPage()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: "YouTube'a yükle" })).toBeDisabled(),
    )
    expect(screen.getByRole('button', { name: "YouTube'a bağlan" })).toBeEnabled()
  })

  it('shows the channel when connected', async () => {
    await renderPage()

    await waitFor(() => expect(screen.getByText('Vanished Earth')).toBeInTheDocument())
    expect(screen.getByText('UC_test_channel')).toBeInTheDocument()
  })

  it('asks for confirmation before uploading, and summarises the upload', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'publishToYoutube').mockResolvedValue(job())
    await renderPage()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: "YouTube'a yükle" })).toBeEnabled(),
    )
    await user.click(screen.getByRole('button', { name: "YouTube'a yükle" }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('the-dodo_v01.mp4')).toBeInTheDocument()
    expect(
      within(dialog).getByText('The Dodo: A Bird That Never Learned to Run'),
    ).toBeInTheDocument()
    expect(within(dialog).getByText('Gizli')).toBeInTheDocument()
    expect(within(dialog).getByText('Hemen')).toBeInTheDocument()
    expect(within(dialog).getByText('Açık')).toBeInTheDocument()
    expect(api.publishToYoutube).not.toHaveBeenCalled()

    await user.click(within(dialog).getByRole('button', { name: 'Yükle' }))

    await waitFor(() =>
      expect(api.publishToYoutube).toHaveBeenCalledWith('the-dodo', {
        mediaId: 'long:render0001',
        allowDuplicate: false,
      }),
    )
  })

  it('says “yükle ve planla” when a time is set', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getAllByLabelText('İleri tarihe planla')[0]!)

    expect(
      screen.getByRole('button', { name: "YouTube'a yükle ve planla" }),
    ).toBeInTheDocument()
  })

  it('shows live progress while an upload runs', async () => {
    await renderPage()
    usePublishingStore.setState({ job: job() })

    expect(await screen.findByText('Video yükleniyor')).toBeInTheDocument()
    expect(screen.getByText('42%')).toBeInTheDocument()
    expect(screen.getByText(/100.0 MB \/ 256.0 MB/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Yüklemeyi iptal et' })).toBeInTheDocument()
  })

  it('shows the result of a finished upload', async () => {
    await renderPage()
    usePublishingStore.setState({
      job: job({
        status: 'completed',
        phase: 'complete',
        progress: 1,
        videoId: 'vid_0001',
        videoUrl: 'https://youtu.be/vid_0001',
        actualPrivacyStatus: 'private',
        processingStatus: 'processing',
        thumbnailStatus: 'uploaded',
        captionStatus: 'uploaded',
      }),
    })

    expect(await screen.findByText('vid_0001')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'https://youtu.be/vid_0001' })).toBeInTheDocument()
    expect(
      screen.getByText('Video YouTube’a gönderildi, YouTube tarafından işleniyor.'),
    ).toBeInTheDocument()
    expect(screen.getAllByText('yüklendi').length).toBe(2)
  })

  it('offers a retry for only the failed step after a partial failure', async () => {
    await renderPage()
    usePublishingStore.setState({
      job: job({
        status: 'completed',
        phase: 'complete',
        progress: 1,
        videoId: 'vid_0001',
        videoUrl: 'https://youtu.be/vid_0001',
        captionStatus: 'failed',
        captionError: 'Altyazı kabul edilmedi.',
        warnings: ['Altyazı eklenemedi: Altyazı kabul edilmedi.'],
      }),
    })

    expect(
      await screen.findByRole('button', { name: 'Kalan adımları tekrar dene' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Altyazı eklenemedi/)).toBeInTheDocument()
  })
})

describe('duplicate protection', () => {
  it('warns and blocks until the user opts in explicitly', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getPublishDraft).mockResolvedValue(
      draftResponse({ duplicateOf: historyEntry() }),
    )
    await renderPage()

    expect(
      await screen.findByText("Bu dosya daha önce YouTube'a yüklenmiş."),
    ).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: "YouTube'a yükle" })).toBeDisabled(),
    )

    await user.click(screen.getByLabelText(/Yine de yeni video olarak yükle/))

    await waitFor(() =>
      expect(screen.getByRole('button', { name: "YouTube'a yükle" })).toBeEnabled(),
    )
  })
})

describe('the other platforms', () => {
  it('shows all three as not connected, with disabled buttons', async () => {
    await renderPage()

    expect(screen.getByRole('heading', { name: 'Instagram' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Facebook' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'TikTok' })).toBeInTheDocument()
    expect(screen.getAllByText('Bağlantı kurulmadı')).toHaveLength(3)

    for (const label of [
      'Instagram bağlantısı yakında',
      'Facebook bağlantısı yakında',
      'TikTok bağlantısı yakında',
    ]) {
      expect(screen.getByRole('button', { name: label })).toBeDisabled()
    }
  })

  it('still lets their fields be edited', async () => {
    const user = userEvent.setup()
    await renderPage()

    const captions = screen.getAllByLabelText('Reels açıklaması')
    await user.type(captions[0]!, 'Instagram için açıklama')
    expect(captions[0]).toHaveValue('Instagram için açıklama')

    await user.click(screen.getByLabelText(/Duet'e izin ver/))
    expect(screen.getByLabelText(/Duet'e izin ver/)).toBeChecked()
  })

  it('never sends a request for them', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    await renderPage()

    const button = screen.getByRole('button', { name: 'Instagram bağlantısı yakında' })
    await user.click(button).catch(() => undefined)

    const urls = fetchSpy.mock.calls.map((call) => String(call[0]))
    expect(urls.some((url) => url.includes('instagram'))).toBe(false)
    expect(urls.some((url) => url.includes('tiktok'))).toBe(false)
    expect(urls.some((url) => url.includes('facebook'))).toBe(false)
  })
})

describe('history', () => {
  it('lists successful uploads with a way to refresh their state', async () => {
    const user = userEvent.setup()
    vi.mocked(api.publishHistory).mockResolvedValue([historyEntry()])
    vi.spyOn(api, 'refreshPublishHistoryEntry').mockResolvedValue(
      historyEntry({ processingStatus: 'succeeded' }),
    )
    await renderPage()

    expect(await screen.findByText('Yayın geçmişi')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'The Dodo' })).toHaveAttribute(
      'href',
      'https://youtu.be/vid_0001',
    )

    await user.click(screen.getByRole('button', { name: 'Durumu yenile' }))
    await waitFor(() =>
      expect(api.refreshPublishHistoryEntry).toHaveBeenCalledWith('the-dodo', 'entry01'),
    )
  })
})

describe('a changed source file', () => {
  it('warns and blocks the upload', async () => {
    vi.mocked(api.getPublishDraft).mockResolvedValue(
      draftResponse({
        sourceChanged: true,
        sourceChangedReason: 'Dosya boyutu değişmiş.',
      }),
    )
    await renderPage()

    expect(await screen.findByText('⚠ Kaynak dosya değişmiş')).toBeInTheDocument()
    expect(screen.getByText('Dosya boyutu değişmiş.')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: "YouTube'a yükle" })).toBeDisabled(),
    )
  })
})
