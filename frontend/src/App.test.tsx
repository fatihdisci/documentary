import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { api } from '@/api/client'
import { makeProject, seedProject } from '@/test/factories'

beforeEach(() => {
  seedProject(makeProject())
  vi.spyOn(api, 'listProjects').mockResolvedValue([])
  vi.spyOn(api, 'youtubeStatus').mockResolvedValue({
    clientFilePresent: false,
    clientFileName: null,
    availableClientFiles: [],
    tokenPresent: false,
    connected: false,
    needsReconnect: false,
    expired: false,
    scopesSufficient: false,
    missingScopes: [],
    channelId: null,
    channelTitle: null,
    channelThumbnailUrl: null,
    checkedAt: null,
    statusMessage: 'OAuth istemci dosyası bulunamadı.',
    problem: null,
    suggestion: null,
  })
  vi.spyOn(api, 'publishingMedia').mockResolvedValue([])
  vi.spyOn(api, 'publishHistory').mockResolvedValue([])
  vi.spyOn(api, 'activePublishJob').mockResolvedValue(null)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('navigation', () => {
  it('has a Yayınla item, between Kısa video and Ayarlar', () => {
    render(<App />)

    const items = screen
      .getAllByRole('button')
      .map((button) => button.textContent ?? '')
      .filter((label) =>
        ['Videoyu oluştur', 'Kısa video', 'Yayınla', 'Ayarlar', 'Sistem kontrolü'].some((name) =>
          label.includes(name),
        ),
      )

    const order = ['Videoyu oluştur', 'Kısa video', 'Yayınla', 'Ayarlar', 'Sistem kontrolü']
    const found = order.map((name) => items.findIndex((label) => label.includes(name)))
    expect(found.every((index) => index >= 0)).toBe(true)
    expect([...found]).toEqual([...found].sort((a, b) => a - b))
  })

  it('opens the Publish panel when the item is clicked', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: /Yayınla/ }))

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Yayınla', level: 1 })).toBeInTheDocument(),
    )
    expect(screen.getByText('1. Dosya seçin')).toBeInTheDocument()
  })

  it('asks for a project first when none is open', async () => {
    seedProject(null)
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: /Yayınla/ }))

    expect(await screen.findByText('Önce Projeler sekmesinden bir proje açın.')).toBeInTheDocument()
  })
})
