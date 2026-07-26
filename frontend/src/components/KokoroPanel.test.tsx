import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { KokoroPanel } from './KokoroPanel'
import { gradeRank, isKokoroVoice, voiceForProvider } from '@/lib/kokoroVoices'
import { api } from '@/api/client'
import type { KokoroInfo } from '@/api/audio-types'
import { makeSettingsResponse } from '@/test/factories'

function makeInfo(overrides: Partial<KokoroInfo['environment']> = {}): KokoroInfo {
  return {
    status: {
      name: 'kokoro',
      available: true,
      message: 'Hazır.',
      requiresApiKey: false,
      apiKeyConfigured: false,
      supportsRate: true,
      supportsPitch: false,
      supportsWordTimings: true,
      offline: true,
    },
    environment: {
      installed: true,
      modelCached: true,
      espeakAvailable: true,
      device: 'cpu',
      cacheDir: '/home/u/.cache/huggingface/hub/models--hexgrad--Kokoro-82M',
      pipInstall: "pip install 'kokoro>=0.9.4' soundfile",
      espeakInstall: 'brew install espeak-ng',
      repoId: 'hexgrad/Kokoro-82M',
      sampleRate: 24000,
      defaultVoice: 'af_bella',
      torchVersion: '2.13.0',
      ...overrides,
    },
    voices: [
      {
        id: 'af_bella', label: 'Bella', gender: 'Female', grade: 'A-', training: '10+ saat',
        note: 'Sıcak ve dolgun.', langCode: 'a', language: 'Amerikan İngilizcesi',
        locale: 'en-US', wordTimings: true,
      },
      {
        id: 'am_michael', label: 'Michael', gender: 'Male', grade: 'C+', training: '1+ saat',
        note: '', langCode: 'a', language: 'Amerikan İngilizcesi', locale: 'en-US',
        wordTimings: true,
      },
      {
        id: 'jf_alpha', label: 'Alpha', gender: 'Female', grade: 'C+', training: '1+ saat',
        note: '', langCode: 'j', language: 'Japonca', locale: 'ja', wordTimings: false,
      },
    ],
    languages: [
      { code: 'a', label: 'Amerikan İngilizcesi', locale: 'en-US', extraInstall: '', wordTimings: true, voiceCount: 20 },
      { code: 'j', label: 'Japonca', locale: 'ja', extraInstall: 'misaki[ja]', wordTimings: false, voiceCount: 5 },
    ],
    recommended: ['af_bella', 'am_michael'],
    deviceOptions: ['auto', 'cpu', 'mps', 'cuda'],
    minSpeed: 0.5,
    maxSpeed: 2.0,
    setupSteps: ["Modeli kurun: pip install 'kokoro>=0.9.4' soundfile"],
    usageNotes: ['Model indikten sonra internet gerekmez.'],
    inputNotes: ['Düz metin yazın.'],
  }
}

beforeEach(() => {
  vi.spyOn(api, 'getKokoroInfo').mockResolvedValue(makeInfo())
  vi.spyOn(api, 'getSettings').mockResolvedValue(makeSettingsResponse())
})

afterEach(() => vi.restoreAllMocks())

describe('voiceForProvider', () => {
  // Kokoro and Edge voice namespaces do not overlap, so a stale id survives the
  // provider switch and only fails later, during generation.
  it('replaces an Edge voice when switching to Kokoro', () => {
    expect(voiceForProvider('kokoro', 'en-US-AndrewNeural', undefined)).toBe('af_bella')
  })

  it('keeps a Kokoro voice that is already valid', () => {
    expect(voiceForProvider('kokoro', 'am_puck', undefined)).toBe('am_puck')
  })

  it('restores the voice last used with that provider', () => {
    expect(voiceForProvider('kokoro', 'en-US-AndrewNeural', 'bm_george')).toBe('bm_george')
    expect(voiceForProvider('edge', 'af_bella', 'en-GB-RyanNeural')).toBe('en-GB-RyanNeural')
  })

  it('replaces a Kokoro voice when switching back to Edge', () => {
    expect(voiceForProvider('edge', 'af_bella', undefined)).toBe('en-US-AndrewNeural')
  })

  it('ignores a remembered voice that does not fit the target provider', () => {
    expect(voiceForProvider('kokoro', 'en-US-AndrewNeural', 'en-US-GuyNeural')).toBe('af_bella')
  })

  it('leaves the voice alone for imported audio, which has no voices', () => {
    expect(voiceForProvider('imported', 'af_bella', undefined)).toBe('af_bella')
  })
})

describe('isKokoroVoice', () => {
  it.each(['af_bella', 'am_michael', 'bm_george', 'jf_alpha', 'zm_yunxi'])(
    'recognises %s',
    (id) => expect(isKokoroVoice(id)).toBe(true),
  )

  it.each(['en-US-AndrewNeural', 'en-GB-RyanNeural', '', 'Rachel'])(
    'rejects %s',
    (id) => expect(isKokoroVoice(id)).toBe(false),
  )
})

describe('gradeRank', () => {
  it('orders grades best first', () => {
    expect(gradeRank('A')).toBeLessThan(gradeRank('C+'))
    expect(gradeRank('C+')).toBeLessThan(gradeRank('F'))
  })

  it('sorts an ungraded voice last', () => {
    expect(gradeRank('—')).toBeGreaterThan(gradeRank('F'))
  })
})

describe('KokoroPanel', () => {
  it('reports a ready install with its environment', async () => {
    render(<KokoroPanel currentVoice="af_bella" onPickVoice={vi.fn()} />)

    expect(await screen.findByText('hazır')).toBeInTheDocument()
    expect(screen.getByText(/torch 2\.13\.0/)).toBeInTheDocument()
    expect(screen.getByText('indirildi')).toBeInTheDocument()
    expect(screen.getByText('espeak-ng var')).toBeInTheDocument()
  })

  it('tells the user what is missing when Kokoro is not installed', async () => {
    vi.spyOn(api, 'getKokoroInfo').mockResolvedValue(
      makeInfo({ installed: false, modelCached: false, espeakAvailable: false, torchVersion: '' }),
    )
    render(<KokoroPanel currentVoice="af_bella" onPickVoice={vi.fn()} />)

    expect(await screen.findByText('kurulu değil')).toBeInTheDocument()
    expect(screen.getByText(/Kokoro kurulu olmadan seçilemez/)).toBeInTheDocument()
  })

  it('shows the setup commands on demand', async () => {
    const user = userEvent.setup()
    render(<KokoroPanel currentVoice="af_bella" onPickVoice={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: /Kurulum/ }))
    expect(screen.getByText("pip install 'kokoro>=0.9.4' soundfile")).toBeInTheDocument()
  })

  it('picks a voice from the recommended chips', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<KokoroPanel currentVoice="af_bella" onPickVoice={onPick} />)

    await user.click(await screen.findByRole('button', { name: /Michael/ }))
    expect(onPick).toHaveBeenCalledWith('am_michael')
  })

  it('says which languages lose word-level subtitle timing', async () => {
    const user = userEvent.setup()
    render(<KokoroPanel currentVoice="af_bella" onPickVoice={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: /Diller ve sesler/ }))
    expect(screen.getByText('yok — altyazı tahmin edilir')).toBeInTheDocument()
    expect(screen.getByText('misaki[ja]')).toBeInTheDocument()
  })

  it('saves the device choice to app settings', async () => {
    const user = userEvent.setup()
    const update = vi.spyOn(api, 'updateSettings').mockResolvedValue(makeSettingsResponse())
    render(<KokoroPanel currentVoice="af_bella" onPickVoice={vi.fn()} />)

    const select = await screen.findByLabelText("Kokoro'nun çalışacağı birim")
    await user.selectOptions(select, 'mps')

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(expect.objectContaining({ kokoroDevice: 'mps' })),
    )
  })
})
