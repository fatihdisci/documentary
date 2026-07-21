/**
 * Frontend mirror of the backend's Ken Burns easing.
 *
 * The geometry (start/end scale and centre) comes from the `/motion` endpoint —
 * the exact numbers the render uses — so this file only reproduces the
 * smoothstep interpolation documented in `kenburns.sample_transform`. A unit
 * test asserts parity against known values.
 */

import type { SceneMotion } from '@/api/project-types'

export function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

export interface SampledTransform {
  scale: number
  cx: number
  cy: number
}

/** Identical easing to `kenburns.sample_transform` (`3p² − 2p³`). */
export function sampleTransform(m: SceneMotion, progress: number): SampledTransform {
  const p = clamp(progress, 0, 1)
  const eased = p * p * (3 - 2 * p)
  return {
    scale: m.startScale + (m.endScale - m.startScale) * eased,
    cx: m.startX + (m.endX - m.startX) * eased,
    cy: m.startY + (m.endY - m.startY) * eased,
  }
}

export function staticMotion(unitId: string): SceneMotion {
  return {
    unitId,
    kind: 'scene',
    preset: 'static',
    isStatic: true,
    startScale: 1,
    endScale: 1,
    startX: 0.5,
    startY: 0.5,
    endX: 0.5,
    endY: 0.5,
    description: 'Static — no movement',
  }
}
