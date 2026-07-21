import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ScenesPage } from './ScenesPage'
import { api, ApiError } from '@/api/client'
import { useProjectStore } from '@/store/project'
import { makeProject, makeScene, seedProject, apiError } from '@/test/factories'

afterEach(() => {
  vi.restoreAllMocks()
  seedProject(null)
})

describe('ScenesPage', () => {
  it('prompts to open a project when none is loaded', () => {
    seedProject(null)
    render(<ScenesPage />)
    expect(screen.getByText('Open a project first.')).toBeInTheDocument()
  })

  it('shows an empty state when there are no scenes', () => {
    seedProject(makeProject({ scenes: [] }))
    render(<ScenesPage />)
    expect(screen.getByText('No scenes yet')).toBeInTheDocument()
  })

  it('lists scenes and flags what each one is missing', () => {
    seedProject(
      makeProject({
        scenes: [
          makeScene({ id: 's1', title: 'Opening', narration: 'Once upon a time.' }),
          makeScene({ id: 's2', title: 'Habitat', narration: '' }),
        ],
      }),
    )
    render(<ScenesPage />)

    expect(screen.getByText('Opening')).toBeInTheDocument()
    expect(screen.getByText('Habitat')).toBeInTheDocument()
    // s1 has narration but no image/audio; s2 additionally has no narration.
    expect(screen.getAllByText('No image').length).toBe(2)
    expect(screen.getByText('No narration')).toBeInTheDocument()
  })

  it('uploads images and reloads the project', async () => {
    const user = userEvent.setup()
    const upload = vi
      .spyOn(api, 'uploadImages')
      .mockResolvedValue({ images: [], mapping: null })
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    seedProject(makeProject({ scenes: [makeScene({ id: 's1', title: 'Opening' })] }))
    render(<ScenesPage />)

    const input = screen.getByLabelText('Upload scene images')
    const file = new File([new Uint8Array([1, 2, 3])], '01-opening.png', { type: 'image/png' })
    await user.upload(input, file)

    await waitFor(() => expect(upload).toHaveBeenCalledWith('the-dodo', [file]))
  })

  it('surfaces an upload failure through the structured ErrorBox', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'uploadImages').mockRejectedValue(
      new ApiError(
        413,
        apiError({
          code: 'file_too_large',
          message: 'That image exceeds the upload limit.',
          suggestion: 'Compress it or raise the limit in Settings.',
        }),
      ),
    )
    seedProject(makeProject({ scenes: [makeScene({ id: 's1' })] }))
    render(<ScenesPage />)

    const input = screen.getByLabelText('Upload scene images')
    const file = new File([new Uint8Array([1])], 'big.png', { type: 'image/png' })
    await user.upload(input, file)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('That image exceeds the upload limit.')
    expect(alert).toHaveTextContent('Compress it or raise the limit in Settings.')
    // The store carries the structured error, not a bare string.
    expect(useProjectStore.getState().error?.code).toBe('file_too_large')
  })
})
