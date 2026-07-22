import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScenePreview } from './ScenePreview'
import { api } from '@/api/client'
import type { SceneMotion } from '@/api/project-types'
import { makeProject, makeScene } from '@/test/factories'

const motion: SceneMotion = {
  unitId: 's1',
  kind: 'scene',
  preset: 'pan-left-to-right',
  isStatic: false,
  startScale: 1.28,
  endScale: 1.28,
  startX: 0.4,
  startY: 0.5,
  endX: 0.6,
  endY: 0.5,
  description: 'pan right',
}

const scene = makeScene({ id: 's1', title: 'Opening', imageFile: '01-opening.png', sceneDurationSeconds: 5 })
const project = makeProject({ scenes: [scene] })

beforeEach(() => {
  vi.spyOn(api, 'getMotion').mockResolvedValue([motion])
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ScenePreview', () => {
  it('describes the resolved move once motion loads', async () => {
    render(<ScenePreview project={project} scene={scene} />)
    expect(await screen.findByText('pan right')).toBeInTheDocument()
    expect(screen.getByText(/pan-left-to-right/)).toBeInTheDocument()
  })

  it('applies the start-of-move transform to the image', async () => {
    render(<ScenePreview project={project} scene={scene} />)
    await screen.findByText('pan right')
    const img = document.querySelector('.preview-image') as HTMLImageElement
    // scale is constant for a pan; the centre starts at cx=0.4.
    expect(img.style.transform).toContain('scale(1.28)')
  })

  it('scrubbing to the end pans the centre to cx=0.6', async () => {
    render(<ScenePreview project={project} scene={scene} />)
    await screen.findByText('pan right')
    const slider = screen.getByLabelText('Önizlemede ileri geri git')
    fireEvent.change(slider, { target: { value: '1' } })

    const img = document.querySelector('.preview-image') as HTMLImageElement
    // tx = (0.5 - 1.28*0.6) * 100 = -26.8
    expect(img.style.transform).toContain('translate(-26.8%')
    expect(screen.getByText(/5\.0s \/ 5\.0s/)).toBeInTheDocument()
  })

  it('falls back to a static frame when motion cannot be loaded', async () => {
    vi.spyOn(api, 'getMotion').mockRejectedValue(new Error('offline'))
    render(<ScenePreview project={project} scene={scene} />)
    expect(await screen.findByText(/sabit görüntü gösteriliyor/)).toBeInTheDocument()
  })
})
