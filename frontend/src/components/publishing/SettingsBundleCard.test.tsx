import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '@/api/client'
import type { ApiErrorPayload, BundleImportResult } from '@/api/types'
import { SettingsBundleCard } from './SettingsBundleCard'

const PASSPHRASE = 'gizli-parola'

function apiError(overrides: Partial<ApiErrorPayload> = {}) {
  return new ApiError(422, {
    code: 'settings_bundle_passphrase',
    message: 'Paket parolası doğru değil.',
    details: null,
    suggestion: 'Paketi oluştururken kullandığınız parolayı girin.',
    logPath: null,
    context: {},
    ...overrides,
  })
}

function importResult(overrides: Partial<BundleImportResult> = {}): BundleImportResult {
  return {
    settingsApplied: true,
    skippedSettings: [],
    secretsImported: ['tiktok_client_secret'],
    secretsSkipped: [],
    credentialFilesImported: ['youtube-upload-token.json'],
    credentialFilesSkipped: [],
    warnings: [],
    contents: {
      settings: true,
      secrets: 1,
      credentialFiles: 1,
      createdAt: '2026-07-29T10:30:00Z',
    },
    ...overrides,
  }
}

async function selectBundle(user: ReturnType<typeof userEvent.setup>) {
  const file = new File(['encrypted bundle'], 'ayarlar.evbkey', { type: 'application/octet-stream' })
  await user.upload(screen.getByLabelText('Şifreli ayar paketi'), file)
  return file
}

afterEach(() => vi.restoreAllMocks())

describe('SettingsBundleCard', () => {
  it('exports the bundle with the selected credential option', async () => {
    const user = userEvent.setup()
    const exportBundle = vi.spyOn(api, 'exportSettingsBundle').mockResolvedValue({
      blob: new Blob(['encrypted']),
      filename: 'ayarlar.evbkey',
      contents: { secrets: 5, credentialFiles: 3 },
    })
    vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:test'), revokeObjectURL: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    render(<SettingsBundleCard />)

    await user.type(screen.getByLabelText('Dışa aktarma parolası'), PASSPHRASE)
    await user.click(screen.getByRole('checkbox', { name: /OAuth yetkilerini de dahil et/ }))
    await user.click(screen.getByRole('button', { name: 'Dışa aktar' }))

    await waitFor(() => expect(exportBundle).toHaveBeenCalledWith(PASSPHRASE, false))
    expect(click).toHaveBeenCalled()
  })

  it('disables export until the passphrase is at least eight characters', async () => {
    const user = userEvent.setup()
    render(<SettingsBundleCard />)

    const button = screen.getByRole('button', { name: 'Dışa aktar' })
    expect(button).toBeDisabled()
    await user.type(screen.getByLabelText('Dışa aktarma parolası'), 'kisa')
    expect(button).toBeDisabled()
    await user.type(screen.getByLabelText('Dışa aktarma parolası'), 'parola')
    expect(button).toBeEnabled()
  })

  it('inspects a chosen file automatically and shows its counts', async () => {
    const user = userEvent.setup()
    const inspect = vi.spyOn(api, 'inspectSettingsBundle').mockResolvedValue({
      settings: true,
      secrets: 5,
      credentialFiles: 3,
      createdAt: '2026-07-29T10:30:00Z',
    })
    render(<SettingsBundleCard />)

    const file = await selectBundle(user)

    await waitFor(() => expect(inspect).toHaveBeenCalledWith(file))
    expect(await screen.findByText(/Bu paket: 5 anahtar, 3 yetki dosyası/)).toBeInTheDocument()
  })

  it('shows imported names but never secret values', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'inspectSettingsBundle').mockResolvedValue({
      settings: true, secrets: 1, credentialFiles: 1, createdAt: null,
    })
    const importBundle = vi.spyOn(api, 'importSettingsBundle').mockResolvedValue({
      ...importResult(),
      // A defensive server-side accident must not turn a secret value into UI text.
      leakedSecretValue: 'FAKE-secret-value',
    } as BundleImportResult)
    const { container } = render(<SettingsBundleCard />)

    const file = await selectBundle(user)
    await user.type(await screen.findByLabelText('İçe aktarma parolası'), PASSPHRASE)
    await user.click(screen.getByRole('button', { name: 'İçe aktar' }))

    await waitFor(() => expect(importBundle).toHaveBeenCalledWith(file, PASSPHRASE, { overwrite: true, includePaths: false }))
    expect(await screen.findByText('tiktok_client_secret')).toBeInTheDocument()
    expect(container.textContent).not.toContain('FAKE-secret-value')
  })

  it('shows an incorrect passphrase error in ErrorBox', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'inspectSettingsBundle').mockResolvedValue({
      settings: true, secrets: 1, credentialFiles: 0, createdAt: null,
    })
    vi.spyOn(api, 'importSettingsBundle').mockRejectedValue(apiError())
    render(<SettingsBundleCard />)

    await selectBundle(user)
    await user.type(await screen.findByLabelText('İçe aktarma parolası'), PASSPHRASE)
    await user.click(screen.getByRole('button', { name: 'İçe aktar' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Paket parolası doğru değil.')
    expect(screen.getByRole('alert')).toHaveTextContent('Paketi oluştururken kullandığınız parolayı girin.')
  })
})
