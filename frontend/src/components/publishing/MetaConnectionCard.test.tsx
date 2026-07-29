/**
 * The Meta, TikTok and hosting cards on the Settings page.
 *
 * Most of what these tests assert is a *negative*: no credential is ever
 * rendered, no value is ever read back, and nothing is offered that the current
 * connection cannot actually do. The rest is the one-time entry rule — App ID
 * and App Secret are typed once, and changing them takes a deliberate second
 * step.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MediaHostCard } from './MediaHostCard'
import { MetaConnectionCard } from './MetaConnectionCard'
import { TikTokConnectionCard } from './TikTokConnectionCard'
import { api } from '@/api/client'
import type {
  MediaHostStatus,
  MetaConnection,
  TikTokConnection,
} from '@/api/publishing-types'

const APP_ID = '1234567890123456'
const APP_SECRET = 'fakemetaappsecretfortests0000'

function meta(overrides: Partial<MetaConnection> = {}): MetaConnection {
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
    problem: null,
    suggestion: null,
    ...overrides,
  }
}

function tiktok(overrides: Partial<TikTokConnection> = {}): TikTokConnection {
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
    redirectUri: 'https://example.test/api/publishing/tiktok/callback',
    checkedAt: '2026-07-28T09:00:00Z',
    statusMessage: 'Uygulama bilgileri girilmedi.',
    problem: null,
    suggestion: null,
    ...overrides,
  }
}

function host(overrides: Partial<MediaHostStatus> = {}): MediaHostStatus {
  return {
    provider: 'none',
    configured: false,
    endpoint: '',
    bucket: '',
    region: 'auto',
    prefix: 'evb-temp',
    keysPresent: false,
    ttlSeconds: 3600,
    deleteAfterPublish: true,
    statusMessage: 'Tanımlı değil.',
    problem: null,
    suggestion: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.spyOn(api, 'metaStatus').mockResolvedValue(meta())
  vi.spyOn(api, 'tiktokStatus').mockResolvedValue(tiktok())
  vi.spyOn(api, 'mediaHostStatus').mockResolvedValue(host())
  vi.stubGlobal('open', vi.fn())
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('the Meta connection card', () => {
  it('shows the callback URL so it can be pasted into the Meta panel', async () => {
    render(<MetaConnectionCard />)

    const field = (await screen.findByLabelText(
      /OAuth callback adresi/,
    )) as HTMLInputElement
    expect(field).toHaveValue('http://localhost:8756/api/publishing/meta/callback')
    expect(field).toHaveAttribute('readonly')
    expect(screen.getAllByText(/Valid OAuth Redirect/).length).toBeGreaterThan(0)
  })

  it('takes the App ID and App Secret once, then hides the fields', async () => {
    const user = userEvent.setup()
    const save = vi
      .spyOn(api, 'saveMetaAppCredentials')
      .mockResolvedValue(meta({ appConfigured: true, statusMessage: 'Hazır.' }))
    render(<MetaConnectionCard />)

    await user.type(await screen.findByLabelText('App ID'), APP_ID)
    await user.type(screen.getByLabelText('App Secret'), APP_SECRET)
    await user.click(screen.getByRole('button', { name: 'Uygulama bilgilerini kaydet' }))

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        appId: APP_ID,
        appSecret: APP_SECRET,
        replace: false,
      }),
    )
    // Once stored, the fields are gone: there is nothing to read back, and a
    // stray save must not be one keystroke away.
    await waitFor(() => expect(screen.queryByLabelText('App Secret')).not.toBeInTheDocument())
    expect(
      screen.getByRole('button', { name: 'Kimlik bilgilerini değiştir' }),
    ).toBeInTheDocument()
  })

  it('never renders a secret, even when one is configured', async () => {
    const { container } = render(<MetaConnectionCard />)
    vi.mocked(api.metaStatus).mockResolvedValue(
      meta({ appConfigured: true, tokenPresent: true, statusMessage: 'Bağlantı geçerli.' }),
    )

    await screen.findByRole('button', { name: 'Durumu yenile' })

    expect(container.textContent).not.toContain(APP_SECRET)
    expect(container.textContent).not.toContain(APP_ID)
    expect(container.innerHTML).not.toContain('accessToken')
  })

  it('requires an explicit second step before replacing a stored pair', async () => {
    const user = userEvent.setup()
    vi.mocked(api.metaStatus).mockResolvedValue(meta({ appConfigured: true }))
    const save = vi
      .spyOn(api, 'saveMetaAppCredentials')
      .mockResolvedValue(meta({ appConfigured: true }))
    render(<MetaConnectionCard />)

    await user.click(
      await screen.findByRole('button', { name: 'Kimlik bilgilerini değiştir' }),
    )
    await user.type(screen.getByLabelText('App ID'), APP_ID)
    await user.type(screen.getByLabelText('App Secret'), APP_SECRET)
    await user.click(screen.getByRole('button', { name: 'Uygulama bilgilerini kaydet' }))

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(expect.objectContaining({ replace: true })),
    )
  })

  it('refuses to start OAuth before the application is configured', async () => {
    const start = vi.spyOn(api, 'startMetaConnect')
    render(<MetaConnectionCard />)

    const button = await screen.findByRole('button', { name: "Meta'ya bağlan" })
    expect(button).toBeDisabled()
    expect(start).not.toHaveBeenCalled()
  })

  it('opens the authorization URL the backend built', async () => {
    const user = userEvent.setup()
    vi.mocked(api.metaStatus).mockResolvedValue(meta({ appConfigured: true }))
    vi.spyOn(api, 'startMetaConnect').mockResolvedValue({
      authorizationUrl: 'https://www.facebook.com/v21.0/dialog/oauth?client_id=1',
      redirectUri: 'http://localhost:8756/api/publishing/meta/callback',
    })
    render(<MetaConnectionCard />)

    await user.click(await screen.findByRole('button', { name: "Meta'ya bağlan" }))

    await waitFor(() =>
      expect(window.open).toHaveBeenCalledWith(
        'https://www.facebook.com/v21.0/dialog/oauth?client_id=1',
        '_blank',
        'noopener,noreferrer',
      ),
    )
  })

  it('asks which Page to publish to when there is more than one', async () => {
    const user = userEvent.setup()
    vi.mocked(api.metaStatus).mockResolvedValue(
      meta({
        appConfigured: true,
        tokenPresent: true,
        scopesSufficient: true,
        pages: [
          {
            pageId: '111',
            name: 'Vanished Earth Docs',
            instagramId: '444',
            instagramUsername: 'vanishedearthdocs',
          },
          { pageId: '222', name: 'Another Page', instagramId: null, instagramUsername: null },
        ],
      }),
    )
    const select = vi.spyOn(api, 'selectMetaPage').mockResolvedValue(meta())
    render(<MetaConnectionCard />)

    const dropdown = await screen.findByLabelText(/Yayın yapılacak Facebook Sayfası/)
    // The one without Instagram says so, rather than silently failing later.
    expect(dropdown).toHaveTextContent('Another Page — Instagram yok')

    await user.selectOptions(dropdown, '222')
    await waitFor(() => expect(select).toHaveBeenCalledWith('222'))
  })
})

describe('the TikTok connection card', () => {
  it('states the audit restriction instead of implying public posting', async () => {
    vi.mocked(api.tiktokStatus).mockResolvedValue(
      tiktok({
        appConfigured: true,
        tokenPresent: true,
        connected: true,
        scopesSufficient: true,
        displayName: 'Vanished Earth Docs',
        auditRequired: true,
      }),
    )
    render(<TikTokConnectionCard />)

    expect(await screen.findByText(/Content Posting API audit/)).toBeInTheDocument()
    expect(screen.getByText(/Yalnızca “Yalnızca ben”/)).toBeInTheDocument()
  })

  it('says plainly that TikTok requires an https redirect', async () => {
    const { container } = render(<TikTokConnectionCard />)

    await screen.findByLabelText(/OAuth callback adresi/)
    // The limitation is stated, not worked around: a loopback backend cannot
    // serve https, so the user has to point an https address at it.
    expect(container.textContent).toContain('yalnızca')
    expect(container.textContent).toContain('https')
    expect(container.textContent).toContain('tünel')
  })

  it('never renders the client secret', async () => {
    const user = userEvent.setup()
    const save = vi
      .spyOn(api, 'saveTiktokAppCredentials')
      .mockResolvedValue(tiktok({ appConfigured: true }))
    const { container } = render(<TikTokConnectionCard />)

    await user.type(await screen.findByLabelText('Client Key'), 'awfaketiktokkey123')
    await user.type(screen.getByLabelText('Client Secret'), 'fakeTikTokClientSecret')
    await user.click(screen.getByRole('button', { name: 'Uygulama bilgilerini kaydet' }))

    await waitFor(() => expect(save).toHaveBeenCalled())
    await waitFor(() =>
      expect(screen.queryByLabelText('Client Secret')).not.toBeInTheDocument(),
    )
    expect(container.textContent).not.toContain('fakeTikTokClientSecret')
  })
})

describe('the temporary hosting card', () => {
  it('explains why it exists and which platforms use it', async () => {
    render(<MediaHostCard />)

    expect(
      await screen.findByText(/videoyu bilgisayarınızdan almaz/),
    ).toBeInTheDocument()
    expect(screen.getByText(/YouTube ve TikTok bu bölümü kullanmaz/)).toBeInTheDocument()
  })

  it('keeps stored keys when only the bucket is changed', async () => {
    const user = userEvent.setup()
    vi.mocked(api.mediaHostStatus).mockResolvedValue(
      host({
        provider: 's3',
        configured: true,
        endpoint: 'https://accountid.r2.cloudflarestorage.com',
        bucket: 'evb-temp',
        keysPresent: true,
      }),
    )
    const save = vi.spyOn(api, 'saveMediaHostSettings').mockResolvedValue(host())
    render(<MediaHostCard />)

    const bucket = await screen.findByLabelText(/Kova \(bucket\) adı/)
    await user.clear(bucket)
    await user.type(bucket, 'evb-renamed')
    await user.click(screen.getByRole('button', { name: 'Barındırma ayarlarını kaydet' }))

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        expect.objectContaining({
          bucket: 'evb-renamed',
          // Empty means "keep what is stored" — correcting a name must not wipe
          // a working credential pair.
          accessKeyId: null,
          secretAccessKey: null,
        }),
      ),
    )
  })

  it('shows that keys exist without showing them', async () => {
    vi.mocked(api.mediaHostStatus).mockResolvedValue(host({ keysPresent: true }))
    const { container } = render(<MediaHostCard />)

    const field = (await screen.findByLabelText('Secret Access Key')) as HTMLInputElement
    expect(field).toHaveAttribute('placeholder', '•••••••• (kayıtlı)')
    expect(field).toHaveValue('')
    expect(field.type).toBe('password')
    expect(container.textContent).not.toMatch(/[A-Z0-9]{20,}/)
  })
})
