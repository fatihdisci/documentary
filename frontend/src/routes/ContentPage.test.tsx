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
    expect(screen.getByText('Önce bir proje açın.')).toBeInTheDocument()
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
        introImage: null,
        unmappedScenes: [],
        unusedImages: [],
        warnings: ['Scene 3 has no image.'],
      },
    })
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    render(<ContentPage />)

    const file = new File(['{}'], 'dodo.json', { type: 'application/json' })
    await user.upload(screen.getByLabelText('Metin dosyası (JSON)'), file)

    expect(await screen.findByText('Yükleme tamamlandı')).toBeInTheDocument()
    expect(screen.getByText('8 sahne oluşturuldu')).toBeInTheDocument()
    expect(screen.getByText('6 görsel eşleştirildi')).toBeInTheDocument()
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
    await user.upload(screen.getByLabelText('Metin dosyası (JSON)'), file)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The content package is not valid JSON.')
    expect(alert).toHaveTextContent('Fix the JSON and try again.')
    expect(alert).toHaveTextContent('/tmp/backend.log')
    // Details are available on demand, not dumped raw.
    await user.click(screen.getByRole('button', { name: /Teknik ayrıntıları/ }))
    expect(screen.getByText('line 4: unexpected token')).toBeInTheDocument()
  })

  it('edits the animal name through the project store', async () => {
    const user = userEvent.setup()
    seedProject(makeProject())
    render(<ContentPage />)

    const input = screen.getByLabelText('Hayvanın adı')
    await user.clear(input)
    await user.type(input, 'Thylacine')

    expect(currentProject().animal.commonName).toBe('Thylacine')
  })
})

describe('the branded opening', () => {
  it('shows the channel opening with the animal as its placeholder', () => {
    seedProject(makeProject())
    render(<ContentPage />)

    expect(screen.getByText('Video açılışı')).toBeInTheDocument()
    expect(screen.getByLabelText(/Üst yazı/)).toHaveAttribute('placeholder', 'Dodo')
    expect(screen.getByLabelText(/Alt yazı/)).toHaveAttribute('placeholder', 'Raphus cucullatus')
    expect(screen.getByLabelText(/Damga yazısı/)).toHaveValue('EXTINCT')
  })

  it('can be turned off, which hides the fields', async () => {
    const user = userEvent.setup()
    seedProject(makeProject())
    render(<ContentPage />)

    await user.click(screen.getByLabelText(/Açılış kartı kullanılsın/))

    expect(currentProject().longIntro?.enabled).toBe(false)
    expect(screen.queryByLabelText(/Damga yazısı/)).not.toBeInTheDocument()
  })

  it('never lets the typewriter or the stamp outlast the intro', async () => {
    const user = userEvent.setup()
    seedProject(makeProject())
    render(<ContentPage />)

    const duration = screen.getByLabelText(/Süre \(sn\)/)
    await user.clear(duration)
    await user.type(duration, '1')

    const intro = currentProject().longIntro!
    expect(intro.duration).toBe(1)
    expect(intro.typewriterDuration).toBeLessThanOrEqual(1)
    expect(intro.stampAt).toBeLessThanOrEqual(1)
  })

  it('works against a backend that has never heard of it', () => {
    const { longIntro: _dropped, ...withoutIntro } = makeProject()
    seedProject(withoutIntro as ReturnType<typeof makeProject>)
    render(<ContentPage />)

    expect(screen.getByLabelText(/Damga yazısı/)).toHaveValue('EXTINCT')
  })

  it('reports what a turnkey package brought in', async () => {
    const user = userEvent.setup()
    seedProject(makeProject())
    vi.spyOn(api, 'importContentFile').mockResolvedValue({
      project: makeProject(),
      report: {
        scenesCreated: 10,
        scenesUpdated: 0,
        scenesRemoved: 0,
        imagesMapped: 11,
        introImage: '00-intro.png',
        unmappedScenes: [],
        unusedImages: [],
        warnings: [],
        longIntroApplied: true,
        ttsApplied: true,
        shortsWithHook: 3,
      },
    })
    vi.spyOn(api, 'getProject').mockResolvedValue({ project: makeProject(), images: [] })
    render(<ContentPage />)

    await user.upload(
      screen.getByLabelText('Metin dosyası (JSON)'),
      new File(['{}'], 'dodo.json', { type: 'application/json' }),
    )

    expect(await screen.findByText('Video açılışı dosyadan alındı')).toBeInTheDocument()
    expect(screen.getByText('Seslendirme sesi dosyadan alındı')).toBeInTheDocument()
    expect(screen.getByText('3 kısa videonun açılış metni hazır')).toBeInTheDocument()
  })
})
