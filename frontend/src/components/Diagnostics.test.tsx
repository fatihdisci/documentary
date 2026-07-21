import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Diagnostics } from './Diagnostics'
import type { DiagnosticsReport } from '@/api/types'

const healthyReport: DiagnosticsReport = {
  generatedAt: 1_700_000_000,
  healthy: true,
  checks: [
    {
      id: 'ffmpeg',
      label: 'FFmpeg',
      status: 'ok',
      value: 'ffmpeg version 8.1.1',
      detail: '/opt/homebrew/bin/ffmpeg',
      suggestion: '',
    },
    {
      id: 'text-engine',
      label: 'Text rendering engine',
      status: 'ok',
      value: 'Pillow (bundled fonts)',
      detail: 'drawtext: NOT available · libass: NOT available.',
      suggestion: '',
    },
  ],
  notes: ["This FFmpeg build has no 'drawtext' filter."],
}

function mockFetchOnce(body: unknown, ok = true, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status,
      statusText: ok ? 'OK' : 'Error',
      json: async () => body,
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Diagnostics', () => {
  it('renders every check with its measured value', async () => {
    mockFetchOnce(healthyReport)
    render(<Diagnostics />)

    expect(await screen.findByText('FFmpeg')).toBeInTheDocument()
    expect(screen.getByText('ffmpeg version 8.1.1')).toBeInTheDocument()
    expect(screen.getByText('Ready to render.')).toBeInTheDocument()
  })

  it('explains the Pillow text engine and the missing drawtext filter', async () => {
    mockFetchOnce(healthyReport)
    render(<Diagnostics />)

    expect(await screen.findByText('Pillow (bundled fonts)')).toBeInTheDocument()
    expect(screen.getByText(/drawtext: NOT available/)).toBeInTheDocument()
    expect(screen.getByText(/no 'drawtext' filter/)).toBeInTheDocument()
  })

  it('reports an unhealthy environment instead of claiming readiness', async () => {
    mockFetchOnce({
      ...healthyReport,
      healthy: false,
      checks: [
        {
          id: 'ffmpeg',
          label: 'FFmpeg',
          status: 'fail',
          value: 'not found',
          detail: '',
          suggestion: 'Install FFmpeg with brew install ffmpeg.',
        },
      ],
    })
    render(<Diagnostics />)

    expect(await screen.findByText(/Not ready to render/)).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText(/Install FFmpeg with brew install ffmpeg\./)).toBeInTheDocument()
  })

  it('surfaces a backend failure with a suggestion and a retry, never a bare error', async () => {
    mockFetchOnce(
      {
        code: 'ffmpeg_not_found',
        message: 'ffmpeg could not be found.',
        details: 'PATH=/usr/bin',
        suggestion: 'Install FFmpeg (brew install ffmpeg).',
        logPath: '/tmp/backend.log',
        context: {},
      },
      false,
      503,
    )
    render(<Diagnostics />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('ffmpeg could not be found.')).toBeInTheDocument()
    expect(screen.getByText(/Install FFmpeg \(brew install ffmpeg\)\./)).toBeInTheDocument()
    expect(screen.getByText('/tmp/backend.log')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('reveals technical details on demand', async () => {
    const user = userEvent.setup()
    mockFetchOnce(
      {
        code: 'render_failed',
        message: 'FFmpeg failed.',
        details: 'stderr: Invalid argument',
        suggestion: 'Check the log.',
        logPath: null,
        context: {},
      },
      false,
      500,
    )
    render(<Diagnostics />)

    await user.click(await screen.findByRole('button', { name: /Show technical details/ }))
    expect(screen.getByText('stderr: Invalid argument')).toBeInTheDocument()
  })

  it('turns a network failure into an actionable message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Failed to fetch')))
    render(<Diagnostics />)

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByText(/Could not reach the backend/)).toBeInTheDocument()
    expect(screen.getByText(/backend is running/)).toBeInTheDocument()
  })
})
