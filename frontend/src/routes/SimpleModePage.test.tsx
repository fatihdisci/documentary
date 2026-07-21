import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SimpleModePage } from './SimpleModePage'
import { api } from '@/api/client'
import { useRenderStore } from '@/store/render'
import type { PreflightResponse, RenderJob } from '@/api/render-types'
import { makeProject, makeScene, seedProject } from '@/test/factories'

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

const noop = () => {}

beforeEach(() => {
  resetRenderStore()
  vi.spyOn(api, 'saveProject').mockImplementation(async (_slug, p) => ({ project: p, images: [] }))
  vi.spyOn(api, 'listProviders').mockResolvedValue({ providers: [] })
  vi.spyOn(api, 'listVoices').mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
  resetRenderStore()
  seedProject(null)
})

describe('SimpleModePage', () => {
  it('prompts to open a project when none is loaded', () => {
    seedProject(null)
    render(<SimpleModePage goTo={noop} />)
    expect(screen.getByText('Open a project first.')).toBeInTheDocument()
  })

  it('marks the content step done when scenes exist', () => {
    seedProject(makeProject({ scenes: [makeScene({ id: 's1' }), makeScene({ id: 's2' })] }))
    render(<SimpleModePage goTo={noop} />)
    expect(screen.getByText(/2 scenes ready/)).toBeInTheDocument()
  })

  it('navigates between steps via the stepper chips', async () => {
    const user = userEvent.setup()
    seedProject(makeProject({ scenes: [makeScene({ id: 's1', narration: 'Hi.' })] }))
    render(<SimpleModePage goTo={noop} />)

    await user.click(screen.getByRole('button', { name: /Narration/ }))
    expect(screen.getByText('5 · Narration')).toBeInTheDocument()
  })

  it('generates narration from the narration step', async () => {
    const user = userEvent.setup()
    const generate = vi.spyOn(api, 'generateNarration').mockResolvedValue({
      project: makeProject(),
      results: [],
      generatedCount: 1,
      reusedCount: 0,
      timing: {},
    })
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    seedProject(makeProject({ scenes: [makeScene({ id: 's1', narration: 'Hello.' })] }))
    render(<SimpleModePage goTo={noop} />)

    await user.click(screen.getByRole('button', { name: /Narration/ }))
    await user.click(screen.getByRole('button', { name: /Generate narration/ }))
    await waitFor(() => expect(generate).toHaveBeenCalledWith('the-dodo', [], false))
  })

  it('shows a ready preflight and starts a render from the last step', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('EventSource', class { close() {} })
    const preflight: PreflightResponse = {
      ready: true,
      blockingIssues: [],
      warnings: [],
      timing: { totalFormatted: '4:30' },
      disk: {},
      transitions: [],
      estimatedRenderSeconds: 120,
    }
    vi.spyOn(api, 'preflight').mockResolvedValue(preflight)
    const startRender = vi
      .spyOn(api, 'startRender')
      .mockResolvedValue({ id: 'j1', status: 'queued', projectSlug: 'the-dodo' } as RenderJob)
    vi.spyOn(api, 'projectRenders').mockResolvedValue([])
    vi.spyOn(api, 'listExports').mockResolvedValue([])
    seedProject(makeProject({ scenes: [makeScene({ id: 's1', narration: 'Hi.' })] }))
    render(<SimpleModePage goTo={noop} />)

    await user.click(screen.getByRole('button', { name: /Render/ }))
    expect(await screen.findByText(/Ready — about 4:30 long/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Render video' }))
    await waitFor(() => expect(startRender).toHaveBeenCalledWith('the-dodo', undefined))
  })
})
