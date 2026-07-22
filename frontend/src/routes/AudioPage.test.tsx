import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AudioPage } from './AudioPage'
import { api, ApiError } from '@/api/client'
import { useProjectStore } from '@/store/project'
import type { TimingResponse, TTSProviderStatus, Voice } from '@/api/audio-types'
import { makeProject, makeScene, seedProject, apiError } from '@/test/factories'

const providers: TTSProviderStatus[] = [
  {
    name: 'edge',
    available: true,
    message: 'Free, needs internet.',
    requiresApiKey: false,
    apiKeyConfigured: false,
    supportsRate: true,
    supportsPitch: true,
    supportsWordTimings: true,
    offline: false,
  },
]

const voices: Voice[] = [
  { id: 'en-US-GuyNeural', name: 'Guy', locale: 'en-US', gender: 'Male', description: '' },
  { id: 'en-GB-RyanNeural', name: 'Ryan', locale: 'en-GB', gender: 'Male', description: '' },
]

const timing: TimingResponse = {
  summary: {
    totalFormatted: '5:12',
    narrationSeconds: 280,
    transitionSeconds: 4,
    introSeconds: 8,
    outroSeconds: 10,
    differenceSeconds: 12,
  },
  entries: [],
  warnings: [],
  cueCount: 24,
}

beforeEach(() => {
  vi.spyOn(api, 'saveProject').mockImplementation(async (_slug, p) => ({ project: p, images: [] }))
  vi.spyOn(api, 'listProviders').mockResolvedValue({ providers })
  vi.spyOn(api, 'listVoices').mockResolvedValue(voices)
  vi.spyOn(api, 'getTiming').mockResolvedValue(timing)
})

afterEach(() => {
  vi.restoreAllMocks()
  seedProject(null)
})

describe('AudioPage', () => {
  it('prompts to open a project when none is loaded', () => {
    seedProject(null)
    render(<AudioPage />)
    expect(screen.getByText('Önce bir proje açın.')).toBeInTheDocument()
  })

  it('shows the computed runtime and the number of scenes missing audio', async () => {
    seedProject(
      makeProject({
        scenes: [
          makeScene({ id: 's1', title: 'One', narration: 'Has text.' }),
          makeScene({ id: 's2', title: 'Two', narration: 'Also text.' }),
        ],
      }),
    )
    render(<AudioPage />)

    expect(await screen.findByText('5:12')).toBeInTheDocument()
    expect(screen.getByText('24')).toBeInTheDocument()
    // Both scenes have narration but no audio yet.
    expect(screen.getByRole('button', { name: /Eksikleri seslendir \(2\)/ })).toBeInTheDocument()
  })

  it('generates narration for the whole project', async () => {
    const user = userEvent.setup()
    const generate = vi.spyOn(api, 'generateNarration').mockResolvedValue({
      project: makeProject(),
      results: [],
      generatedCount: 2,
      reusedCount: 0,
      timing: {},
    })
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    seedProject(makeProject({ scenes: [makeScene({ id: 's1', narration: 'Text.' })] }))
    render(<AudioPage />)

    await user.click(await screen.findByRole('button', { name: /Eksikleri seslendir/ }))
    await waitFor(() => expect(generate).toHaveBeenCalledWith('the-dodo', [], false))
  })

  it('surfaces a generation failure through the structured ErrorBox', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'generateNarration').mockRejectedValue(
      new ApiError(
        502,
        apiError({
          code: 'tts_failed',
          message: 'Edge TTS could not be reached.',
          suggestion: 'Check your internet connection and try again.',
        }),
      ),
    )
    seedProject(makeProject({ scenes: [makeScene({ id: 's1', narration: 'Text.' })] }))
    render(<AudioPage />)

    await user.click(await screen.findByRole('button', { name: /Eksikleri seslendir/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Edge TTS could not be reached.')
    expect(alert).toHaveTextContent('Check your internet connection and try again.')
  })

  it('changes the voice through the project store', async () => {
    const user = userEvent.setup()
    seedProject(makeProject({ scenes: [makeScene({ id: 's1', narration: 'Text.' })] }))
    render(<AudioPage />)

    // Wait for voices to load, then pick a different one.
    await screen.findByText(/konuşmacı var/)
    const voiceSelect = screen
      .getAllByRole('combobox')
      .find((el) => (el as HTMLSelectElement).value === 'en-US-GuyNeural')!
    await user.selectOptions(voiceSelect, 'en-GB-RyanNeural')
    expect(useProjectStore.getState().project?.audio.voice).toBe('en-GB-RyanNeural')
  })
})
