import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ExportPage } from './ExportPage'
import { api, ApiError } from '@/api/client'
import { useRenderStore } from '@/store/render'
import type { PreflightResponse, RenderJob } from '@/api/render-types'
import { makeProject, seedProject, apiError } from '@/test/factories'

function preflight(overrides: Partial<PreflightResponse> = {}): PreflightResponse {
  return {
    ready: true,
    blockingIssues: [],
    warnings: [],
    timing: { totalFormatted: '4:30', sceneCount: 8, transitionSeconds: 4 },
    disk: { totalMb: 2048 },
    transitions: [],
    estimatedRenderSeconds: 180,
    ...overrides,
  }
}

function resetRenderStore() {
  useRenderStore.setState({
    preflight: null,
    job: null,
    event: null,
    history: [],
    exports: [],
    error: null,
    busy: false,
  })
}

beforeEach(() => {
  resetRenderStore()
  vi.spyOn(api, 'saveProject').mockImplementation(async (_slug, p) => ({ project: p, images: [] }))
  vi.spyOn(api, 'projectRenders').mockResolvedValue([])
  vi.spyOn(api, 'listExports').mockResolvedValue([])
  vi.spyOn(api, 'activeJob').mockResolvedValue(null)
})

afterEach(() => {
  vi.restoreAllMocks()
  resetRenderStore()
  seedProject(null)
})

describe('ExportPage', () => {
  it('prompts to open a project when none is loaded', () => {
    seedProject(null)
    vi.spyOn(api, 'preflight').mockResolvedValue(preflight())
    render(<ExportPage />)
    expect(screen.getByText('Önce bir proje açın.')).toBeInTheDocument()
  })

  it('shows a ready preflight and enables the render button', async () => {
    seedProject(makeProject())
    vi.spyOn(api, 'preflight').mockResolvedValue(preflight())
    render(<ExportPage />)

    expect(await screen.findByText(/Her şey hazır, videoyu oluşturabilirsiniz/)).toBeInTheDocument()
    expect(screen.getByText('4:30')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Videoyu oluştur' })).toBeEnabled()
  })

  it('lists blocking issues and disables the render button', async () => {
    seedProject(makeProject())
    vi.spyOn(api, 'preflight').mockResolvedValue(
      preflight({ ready: false, blockingIssues: ['Scene 3 has no image.'] }),
    )
    render(<ExportPage />)

    expect(await screen.findByText(/Önce bunların çözülmesi gerekiyor/)).toBeInTheDocument()
    expect(screen.getByText(/Scene 3 has no image\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Videoyu oluştur' })).toBeDisabled()
  })

  it('starts a render when the button is pressed', async () => {
    const user = userEvent.setup()
    // Keep attach() from opening a real EventSource in jsdom.
    vi.stubGlobal(
      'EventSource',
      class {
        close() {}
      },
    )
    seedProject(makeProject())
    vi.spyOn(api, 'preflight').mockResolvedValue(preflight())
    const job = { id: 'job1', status: 'queued', projectSlug: 'the-dodo' } as RenderJob
    const startRender = vi.spyOn(api, 'startRender').mockResolvedValue(job)
    render(<ExportPage />)

    await user.click(await screen.findByRole('button', { name: 'Videoyu oluştur' }))
    await waitFor(() => expect(startRender).toHaveBeenCalledWith('the-dodo', undefined))
  })

  it('surfaces a preflight failure through the structured ErrorBox', async () => {
    seedProject(makeProject())
    vi.spyOn(api, 'preflight').mockRejectedValue(
      new ApiError(
        503,
        apiError({
          code: 'ffprobe_not_found',
          message: 'ffprobe could not be found.',
          suggestion: 'Install FFmpeg (brew install ffmpeg).',
        }),
      ),
    )
    render(<ExportPage />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('ffprobe could not be found.')
    expect(alert).toHaveTextContent('Install FFmpeg (brew install ffmpeg).')
  })
})
