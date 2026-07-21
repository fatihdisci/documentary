import { describe, expect, it } from 'vitest'
import { sampleTransform, staticMotion } from './kenburns'
import type { SceneMotion } from '@/api/project-types'

// A left-to-right pan at the backend's PAN_SCALE. The expected values are the
// output of backend `kenburns.sample_transform` for the same motion — this test
// is the parity guard the backend docstring refers to.
const pan: SceneMotion = {
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

describe('sampleTransform (backend parity)', () => {
  const expected: Array<[number, number, number, number]> = [
    // progress, scale, cx, cy
    [0.0, 1.28, 0.4, 0.5],
    [0.25, 1.28, 0.43125, 0.5],
    [0.5, 1.28, 0.5, 0.5],
    [0.75, 1.28, 0.56875, 0.5],
    [1.0, 1.28, 0.6, 0.5],
  ]

  it.each(expected)('matches the backend at p=%s', (p, scale, cx, cy) => {
    const t = sampleTransform(pan, p)
    expect(t.scale).toBeCloseTo(scale, 6)
    expect(t.cx).toBeCloseTo(cx, 6)
    expect(t.cy).toBeCloseTo(cy, 6)
  })

  it('clamps progress outside [0, 1]', () => {
    expect(sampleTransform(pan, -1).cx).toBeCloseTo(0.4, 6)
    expect(sampleTransform(pan, 2).cx).toBeCloseTo(0.6, 6)
  })

  it('a static motion never moves', () => {
    const s = staticMotion('x')
    expect(sampleTransform(s, 0.5)).toEqual({ scale: 1, cx: 0.5, cy: 0.5 })
  })
})
