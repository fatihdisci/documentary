import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ContentPage } from './ContentPage'
import { api, ApiError } from '@/api/client'
import { useProjectStore } from '@/store/project'
import { makeProject, seedProject, apiError } from '@/test/factories'

function currentProject() {
  const p = useProjectStore.getState().project
  if (!p) throw new Error('no project in store')
  return p
}

beforeEach(() => {
  vi.spyOn(api, 'saveProject').mockImplementation(async (_slug, p) => ({ project: p, images: [] }))
})

afterEach(() => {
  vi.restoreAllMocks()
  seedProject(null)
})

describe('ContentPage', () => {
  it('prompts to open a project when none is loaded', () => {
    seedProject(null)
    render(<ContentPage />)
    expect(screen.getByText('Open a project first.')).toBeInTheDocument()
  })

  it('imports a package and shows the report', async () => {
    const user = userEvent.setup()
    seedProject(makeProject())
    vi.spyOn(api, 'importContentFile').mockResolvedValue({
      project: makeProject(),
      report: {
        scenesCreated: 8,
        scenesUpdated: 0,
        scenesRemoved: 0,
        imagesMapped: 6,
        unmappedScenes: [],
        unusedImages: [],
        warnings: ['Scene 3 has no image.'],
      },
    })
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    render(<ContentPage />)

    const file = new File(['{}'], 'dodo.json', { type: 'application/json' })
    await user.upload(screen.getByLabelText('Content package JSON'), file)

    expect(await screen.findByText('Import complete')).toBeInTheDocument()
    expect(screen.getByText('8 scenes created')).toBeInTheDocument()
    expect(screen.getByText('6 images mapped to scenes')).toBeInTheDocument()
    expect(screen.getByText(/Scene 3 has no image\./)).toBeInTheDocument()
  })

  it('surfaces an import failure through the structured ErrorBox', async () => {
    const user = userEvent.setup()
    seedProject(makeProject())
    vi.spyOn(api, 'importContentFile').mockRejectedValue(
      new ApiError(
        422,
        apiError({
          code: 'schema_validation',
          message: 'The content package is not valid JSON.',
          suggestion: 'Fix the JSON and try again.',
          logPath: '/tmp/backend.log',
          details: 'line 4: unexpected token',
        }),
      ),
    )
    render(<ContentPage />)

    const file = new File(['not json'], 'broken.json', { type: 'application/json' })
    await user.upload(screen.getByLabelText('Content package JSON'), file)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The content package is not valid JSON.')
    expect(alert).toHaveTextContent('Fix the JSON and try again.')
    expect(alert).toHaveTextContent('/tmp/backend.log')
    // Details are available on demand, not dumped raw.
    await user.click(screen.getByRole('button', { name: /technical details/ }))
    expect(screen.getByText('line 4: unexpected token')).toBeInTheDocument()
  })

  it('edits the animal name through the project store', async () => {
    const user = userEvent.setup()
    seedProject(makeProject())
    render(<ContentPage />)

    const input = screen.getByLabelText('Animal name')
    await user.clear(input)
    await user.type(input, 'Thylacine')

    expect(currentProject().animal.commonName).toBe('Thylacine')
  })
})
