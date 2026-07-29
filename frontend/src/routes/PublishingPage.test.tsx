import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PublishingPage } from './PublishingPage'
import { api } from '@/api/client'
import { usePublishingStore } from '@/store/publishing'
import type {
  DraftResponse,
  MediaHostStatus,
  MediaItem,
  MetaConnection,
  PublishDraft,
  PublishHistoryEntry,
  PublishJob,
  TikTokConnection,
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
    contentPlanId: null,
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
      categoryId: '15',
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
    instagram: {
      caption: '', hashtags: [], account: '', publishMode: 'now', publishAtLocal: null,
      shareToFeed: true,
    },
    facebook: { caption: '', hashtags: [], account: '', publishMode: 'now', publishAtLocal: null },
    tiktok: {
      caption: '',
      hashtags: [],
      account: '',
      publishMode: 'now',
      publishAtLocal: null,
      privacy: 'SELF_ONLY',
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
    duplicates: {},
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

function metaConnection(overrides: Partial<MetaConnection> = {}): MetaConnection {
  return {
    appConfigured: false,
    tokenPresent: false,
    connected: false,
    needsReconnect: false,
    expired: false,
    expiresAt: null,
    scopesSufficient: false,
    missingScopes: [],
    pages: [],
    selectedPageId: null,
    pageName: null,
    instagramId: null,
    instagramUsername: null,
    redirectUri: 'http://localhost:8756/api/publishing/meta/callback',
    checkedAt: '2026-07-28T09:00:00Z',
    statusMessage: 'Uygulama bilgileri girilmedi.',
    problem: 'Meta App ID ve App Secret henüz kaydedilmemiş.',
    suggestion: 'Meta Developer panelindeki değerleri girin.',
    ...overrides,
  }
}

/** A fully working Meta connection: page chosen, Instagram linked. */
function connectedMeta(overrides: Partial<MetaConnection> = {}): MetaConnection {
  return metaConnection({
    appConfigured: true,
    tokenPresent: true,
    connected: true,
    scopesSufficient: true,
    pages: [
      {
        pageId: '111222333',
        name: 'Vanished Earth Docs',
        instagramId: '444555666',
        instagramUsername: 'vanishedearthdocs',
      },
    ],
    selectedPageId: '111222333',
    pageName: 'Vanished Earth Docs',
    instagramId: '444555666',
    instagramUsername: 'vanishedearthdocs',
    statusMessage: 'Bağlantı geçerli.',
    problem: null,
    suggestion: null,
    ...overrides,
  })
}

function tiktokConnection(overrides: Partial<TikTokConnection> = {}): TikTokConnection {
  return {
    appConfigured: false,
    tokenPresent: false,
    connected: false,
    needsReconnect: false,
    expired: false,
    expiresAt: null,
    scopesSufficient: false,
    missingScopes: [],
    displayName: null,
    avatarUrl: null,
    creatorInfo: null,
    auditRequired: true,
    redirectUri: 'http://localhost:8756/api/publishing/tiktok/callback',
    checkedAt: '2026-07-28T09:00:00Z',
    statusMessage: 'Uygulama bilgileri girilmedi.',
    problem: null,
    suggestion: null,
    ...overrides,
  }
}

function hostStatus(overrides: Partial<MediaHostStatus> = {}): MediaHostStatus {
  return {
    provider: 'none',
    configured: false,
    endpoint: '',
    bucket: '',
    region: '',
    prefix: '',
    keysPresent: false,
    ttlSeconds: 0,
    deleteAfterPublish: true,
    statusMessage: 'Tanımlı değil.',
    problem: 'Geçici bir barındırma alanı gerekir.',
    suggestion: 'Bir R2 kovası tanımlayın.',
    ...overrides,
  }
}

function configuredHost(): MediaHostStatus {
  return hostStatus({
    provider: 's3',
    configured: true,
    endpoint: 'https://accountid.r2.cloudflarestorage.com',
    bucket: 'evb-temp',
    region: 'auto',
    prefix: 'reels',
    keysPresent: true,
    ttlSeconds: 3600,
    statusMessage: 'Hazır.',
    problem: null,
    suggestion: null,
  })
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
    containerId: null,
    hostedObjectKey: null,
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
    duplicates: {},
    connection: null,
    meta: null,
    tiktok: null,
    mediaHost: null,
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
  // The three platform cards read their own connection state. The default is a
  // fresh install: nothing configured, so every card must say so rather than
  // offering a button that would fail.
  vi.spyOn(api, 'metaStatus').mockResolvedValue(metaConnection())
  vi.spyOn(api, 'tiktokStatus').mockResolvedValue(tiktokConnection())
  vi.spyOn(api, 'mediaHostStatus').mockResolvedValue(hostStatus())
  vi.spyOn(api, 'publishToPlatform').mockResolvedValue(
    job({ id: 'igjob01', platform: 'instagram' }),
  )
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
  const result = render(<PublishingPage />)
  // The filename also appears in the history table, so match all of them.
  await waitFor(() => expect(screen.getAllByText('the-dodo_v01.mp4').length).toBeGreaterThan(0))
  return result
}

// --- tests ------------------------------------------------------------------

describe('media selection', () => {
  it('lists both long videos and Shorts with their details', async () => {
    render(<PublishingPage />)
    await screen.findByText('the-dodo-short-aaaa1111.mp4')

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
  it('shows the matching Shorts plan and its prefilled platform copy', async () => {
    const plannedProject = makeProject({
      shortsPlan: {
        version: 1,
        captionMode: 'shorts-native',
        captionPreset: 'large',
        recommendedReleaseOrder: ['scenes-two-three'],
        shorts: [
          {
            id: 'scenes-two-three',
            priority: 1,
            purpose: 'A compact extinction hook.',
            sections: [
              { kind: 'scene', number: 2 },
              { kind: 'scene', number: 3 },
            ],
            estimatedDurationSeconds: 38,
            youtube: {
              title: 'Why the Dodo Had No Fear',
              alternativeTitles: ['A Bird Without Fear'],
              description: 'YouTube planned description',
              tags: ['dodo short'],
              hashtags: ['#Dodo'],
              pinnedComment: 'The dodo was not foolish.',
            },
            instagram: { caption: 'Instagram planned copy', hashtags: ['#Dodo'], cta: '' },
            facebook: { caption: 'Facebook planned copy', hashtags: [], cta: '' },
            tiktok: { caption: 'TikTok planned copy', hashtags: [], cta: '' },
          },
        ],
      },
    })
    seedProject(plannedProject)
    const media = shortMedia({ contentPlanId: 'scenes-two-three' })
    const plannedDraft = draft({
      mediaId: media.mediaId,
      youtube: {
        ...draft().youtube,
        title: 'Why the Dodo Had No Fear',
        description: 'YouTube planned description',
        tags: ['dodo short'],
      },
      instagram: {
        ...draft().instagram,
        caption: 'Instagram planned copy',
        hashtags: ['#Dodo'],
      },
      facebook: { ...draft().facebook, caption: 'Facebook planned copy' },
      tiktok: { ...draft().tiktok, caption: 'TikTok planned copy' },
    })
    vi.mocked(api.publishingMedia).mockResolvedValue([media])
    vi.mocked(api.getPublishDraft).mockResolvedValue(
      draftResponse({ draft: plannedDraft, media }),
    )

    render(<PublishingPage />)
    await screen.findByText('the-dodo-short-aaaa1111.mp4')

    expect(screen.getByText('JSON’daki Shorts planı uygulandı')).toBeInTheDocument()
    expect(screen.getByText(/2. sahne → 3. sahne/)).toBeInTheDocument()
    expect(screen.getByText(/A Bird Without Fear/)).toBeInTheDocument()
    expect(screen.getByText(/The dodo was not foolish/)).toBeInTheDocument()
    expect(titleInput()).toHaveValue('Why the Dodo Had No Fear')
    expect(descriptionInput()).toHaveValue('YouTube planned description')
    expect(screen.getByDisplayValue('Instagram planned copy')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Facebook planned copy')).toBeInTheDocument()
    expect(screen.getByDisplayValue('TikTok planned copy')).toBeInTheDocument()
  })

  it('fills the fields from the project metadata', async () => {
    await renderPage()

    expect(titleInput()).toHaveValue(
      'The Dodo: A Bird That Never Learned to Run',
    )
    expect(descriptionInput()).toHaveValue('A documentary about the dodo.')
    expect(screen.getByText('dodo')).toBeInTheDocument()
    expect(screen.getByText('extinct animals')).toBeInTheDocument()
    expect(screen.getByLabelText('Kategori')).toHaveValue('15')
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

describe('edits waiting on the debounce', () => {
  /** The save calls made for one media id, oldest first. */
  function savesFor(mediaId: string) {
    return vi
      .mocked(api.savePublishDraft)
      .mock.calls.filter((call) => call[1] === mediaId)
  }

  it('saves the pending draft before another file is selected', async () => {
    const user = userEvent.setup()
    await renderPage()

    const title = titleInput()
    await user.clear(title)
    await user.type(title, 'Kaydedilmemiş başlık')

    vi.mocked(api.getPublishDraft).mockResolvedValue(
      draftResponse({
        draft: draft({ mediaId: 'short:aaaa1111' }),
        media: shortMedia(),
      }),
    )
    // No waiting: the click lands while the debounce is still holding the edit.
    await user.click(screen.getByText('the-dodo-short-aaaa1111.mp4'))

    await waitFor(() =>
      expect(api.getPublishDraft).toHaveBeenCalledWith('the-dodo', 'short:aaaa1111'),
    )
    const saves = savesFor('long:render0001')
    expect(saves.length).toBeGreaterThan(0)
    expect(saves.at(-1)![2].youtube.title).toBe('Kaydedilmemiş başlık')
    // …and it was saved *before* the new draft replaced it in memory.
    const lastSave = vi.mocked(api.savePublishDraft).mock.invocationCallOrder.at(-1)!
    const shortFetch = vi.mocked(api.getPublishDraft).mock.invocationCallOrder.at(-1)!
    expect(lastSave).toBeLessThan(shortFetch)
  })

  it('never writes one file’s text against another file’s draft', async () => {
    const user = userEvent.setup()
    await renderPage()

    const title = titleInput()
    await user.clear(title)
    await user.type(title, 'Uzun videonun başlığı')

    vi.mocked(api.getPublishDraft).mockResolvedValue(
      draftResponse({
        draft: draft({ mediaId: 'short:aaaa1111' }),
        media: shortMedia(),
      }),
    )
    await user.click(screen.getByText('the-dodo-short-aaaa1111.mp4'))
    await waitFor(() => expect(titleInput()).toHaveValue(draft().youtube.title))

    for (const [, mediaId, value] of vi.mocked(api.savePublishDraft).mock.calls) {
      expect(value.mediaId).toBe(mediaId)
      if (mediaId === 'short:aaaa1111') {
        expect(value.youtube.title).not.toBe('Uzun videonun başlığı')
      }
    }
  })

  it('saves the pending draft when the panel is left', async () => {
    const user = userEvent.setup()
    const { unmount } = await renderPage()

    const title = titleInput()
    await user.clear(title)
    await user.type(title, 'Sekmeden çıkarken')

    unmount()

    await waitFor(() => expect(savesFor('long:render0001').length).toBeGreaterThan(0))
    expect(savesFor('long:render0001').at(-1)![2].youtube.title).toBe('Sekmeden çıkarken')
  })

  it('waits for a save in flight before starting the upload', async () => {
    const user = userEvent.setup()
    let release!: () => void
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.mocked(api.savePublishDraft).mockImplementation(async (_slug, mediaId, value) => {
      await held
      return draftResponse({ draft: { ...value, mediaId } })
    })
    vi.spyOn(api, 'publishToYoutube').mockResolvedValue(job())
    await renderPage()

    const title = titleInput()
    await user.clear(title)
    await user.type(title, 'Yükleme öncesi son hâli')

    // Let the debounce fire, so a save really is on the wire.
    await waitFor(() => expect(api.savePublishDraft).toHaveBeenCalled(), { timeout: 3000 })
    await waitFor(() => expect(usePublishingStore.getState().saveStatus).toBe('saving'))

    await user.click(screen.getByRole('button', { name: "YouTube'a yükle" }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Yükle' }))

    // The job would read the *stored* draft, which is still the old one.
    expect(api.publishToYoutube).not.toHaveBeenCalled()

    release()

    await waitFor(() => expect(api.publishToYoutube).toHaveBeenCalled())
    expect(savesFor('long:render0001').at(-1)![2].youtube.title).toBe(
      'Yükleme öncesi son hâli',
    )
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

describe('the Instagram and Facebook cards', () => {
  it('refuse to publish while nothing is connected, and say why', async () => {
    await renderPage()

    expect(screen.getByRole('heading', { name: 'Instagram' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Facebook' })).toBeInTheDocument()

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Instagram Reels olarak yayınla' }),
      ).toBeDisabled(),
    )
    expect(screen.getByRole('button', { name: 'Facebook Reels olarak yayınla' })).toBeDisabled()
    // The reason is the actual missing piece, not a generic "coming soon".
    expect(screen.getAllByText('Meta uygulama bilgileri girilmedi.').length).toBeGreaterThan(0)
  })

  it('name the missing hosting layer rather than failing mid-upload', async () => {
    vi.mocked(api.metaStatus).mockResolvedValue(connectedMeta())
    await renderPage()

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Instagram Reels olarak yayınla' }),
      ).toBeDisabled(),
    )
    expect(
      screen.getAllByText(/Geçici medya barındırma tanımlı değil/).length,
    ).toBeGreaterThan(0)
  })

  it('show the connected account as the destination', async () => {
    vi.mocked(api.metaStatus).mockResolvedValue(connectedMeta())
    vi.mocked(api.mediaHostStatus).mockResolvedValue(configuredHost())
    await renderPage()

    expect(await screen.findByText('@vanishedearthdocs')).toBeInTheDocument()
    expect(screen.getByText('Vanished Earth Docs')).toBeInTheDocument()
  })

  it('publish a Reel only after the confirmation is accepted', async () => {
    const user = userEvent.setup()
    vi.mocked(api.metaStatus).mockResolvedValue(connectedMeta())
    vi.mocked(api.mediaHostStatus).mockResolvedValue(configuredHost())
    await renderPage()

    const button = await screen.findByRole('button', {
      name: 'Instagram Reels olarak yayınla',
    })
    await waitFor(() => expect(button).toBeEnabled())
    await user.click(button)

    // Nothing is sent while the dialog is open.
    expect(api.publishToPlatform).not.toHaveBeenCalled()
    expect(await screen.findByText('Instagram üzerinde yayınla')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Yayınla' }))

    await waitFor(() =>
      expect(api.publishToPlatform).toHaveBeenCalledWith('the-dodo', 'instagram', {
        mediaId: 'long:render0001',
        allowDuplicate: false,
      }),
    )
  })

  it('send the caption and hashtags the user typed', async () => {
    const user = userEvent.setup()
    vi.mocked(api.metaStatus).mockResolvedValue(connectedMeta())
    vi.mocked(api.mediaHostStatus).mockResolvedValue(configuredHost())
    await renderPage()

    const caption = screen.getAllByLabelText(/Reels açıklaması/)[0]!
    await user.type(caption, 'The last dodo.')

    await waitFor(() =>
      expect(api.savePublishDraft).toHaveBeenCalledWith(
        'the-dodo',
        'long:render0001',
        expect.objectContaining({
          instagram: expect.objectContaining({ caption: 'The last dodo.' }),
        }),
      ),
    )
  })

  it('keep each platform independent when one is already published', async () => {
    const user = userEvent.setup()
    vi.mocked(api.metaStatus).mockResolvedValue(connectedMeta())
    vi.mocked(api.mediaHostStatus).mockResolvedValue(configuredHost())
    vi.mocked(api.getPublishDraft).mockResolvedValue(
      draftResponse({
        duplicates: { instagram: historyEntry({ platform: 'instagram' }) },
      }),
    )
    await renderPage()

    // Instagram is blocked because *Instagram* already has this file…
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Instagram Reels olarak yayınla' }),
      ).toBeDisabled(),
    )
    // …and Facebook is not, because nothing was published there.
    expect(screen.getByRole('button', { name: 'Facebook Reels olarak yayınla' })).toBeEnabled()

    await user.click(screen.getByLabelText(/Yine de yeni gönderi olarak yükle/))
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Instagram Reels olarak yayınla' }),
      ).toBeEnabled(),
    )
  })
})

describe('the TikTok card', () => {
  it('is honest about the audit instead of offering public posting', async () => {
    vi.mocked(api.tiktokStatus).mockResolvedValue(
      tiktokConnection({
        appConfigured: true,
        tokenPresent: true,
        connected: true,
        scopesSufficient: true,
        displayName: 'Vanished Earth Docs',
        auditRequired: true,
        creatorInfo: {
          nickname: 'Vanished Earth Docs',
          username: 'vanishedearthdocs',
          avatarUrl: null,
          privacyLevelOptions: ['SELF_ONLY'],
          commentDisabled: false,
          duetDisabled: false,
          stitchDisabled: false,
          maxVideoPostDurationSeconds: 600,
          fetchedAt: null,
        },
      }),
    )
    await renderPage()

    expect(
      await screen.findByText('Uygulama TikTok denetiminden geçmedi.'),
    ).toBeInTheDocument()
    // The only option offered is the only one TikTok would accept.
    const privacy = screen.getByLabelText(/Gizlilik/, {
      selector: '#tiktok-privacy',
    }) as HTMLSelectElement
    expect([...privacy.options].map((option) => option.value)).toEqual(['SELF_ONLY'])
    expect(screen.getByRole('button', { name: "TikTok'a gönder" })).toBeEnabled()
  })

  it('does not offer a privacy list before the account is connected', async () => {
    await renderPage()

    const privacy = screen.getByLabelText(/Gizlilik/, {
      selector: '#tiktok-privacy',
    }) as HTMLSelectElement
    expect(privacy).toBeDisabled()
    expect(screen.getByRole('button', { name: "TikTok'a gönder" })).toBeDisabled()
    expect(screen.getAllByText('TikTok uygulama bilgileri girilmedi.').length).toBeGreaterThan(0)
  })

  it('still lets the text be edited while disconnected', async () => {
    const user = userEvent.setup()
    await renderPage()

    const caption = screen.getByLabelText(/Başlık \/ açıklama/)
    await user.type(caption, 'Dodo')
    expect(caption).toHaveValue('Dodo')
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
