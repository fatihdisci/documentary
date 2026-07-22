import { describe, expect, it } from 'vitest'
import { formatTimecode, frameAt, resolvePlan, type Selection } from './shortsPlan'
import type { ShortTimelineSection } from '@/api/shorts-types'

/** Intro + four scenes + outro, 10s each with a 0.5s dissolve between them. */
function sections(): ShortTimelineSection[] {
  const spec: [string, ShortTimelineSection['kind'], number, number][] = [
    ['intro', 'intro', 0, 6],
    ['scene-1', 'scene', 1, 10],
    ['scene-2', 'scene', 2, 10],
    ['scene-3', 'scene', 3, 10],
    ['scene-4', 'scene', 4, 10],
    ['outro', 'outro', 5, 5],
  ]
  const built: ShortTimelineSection[] = []
  let cursor = 0
  spec.forEach(([unitId, kind, number, duration], index) => {
    const last = index === spec.length - 1
    const outgoing = last ? 0 : 0.5
    const incoming = index === 0 ? 0 : 0.5
    built.push({
      unitId,
      kind,
      number,
      title: unitId,
      startSeconds: cursor,
      endSeconds: cursor + duration,
      durationSeconds: duration,
      safeStartSeconds: cursor + incoming,
      safeEndSeconds: cursor + duration - outgoing,
      safeDurationSeconds: duration - incoming - outgoing,
      transitionToNext: outgoing ? 'documentary-dissolve' : 'none',
      transitionDurationSeconds: outgoing,
      transitionFromPreviousSeconds: incoming,
      fadeInSeconds: 0,
    })
    cursor += duration - outgoing
  })
  return built
}

function pick(...unitIds: string[]): Selection[] {
  return unitIds.map((unitId) => ({ unitId, startSeconds: null, endSeconds: null }))
}

describe('resolvePlan', () => {
  it('keeps the click order, not the timeline order', () => {
    const plan = resolvePlan(sections(), pick('scene-4', 'scene-1'))
    expect(plan.segments.map((s) => s.number)).toEqual([4, 1])
    expect(plan.groups.map((g) => g.numbers)).toEqual([[4], [1]])
  })

  it('merges adjacent selections into one cut so the transition survives', () => {
    const plan = resolvePlan(sections(), pick('scene-2', 'scene-3'))
    expect(plan.groups).toHaveLength(1)
    expect(plan.groups[0]!.numbers).toEqual([2, 3])
    expect(plan.groups[0]!.preservedTransitions).toBe(1)
  })

  it('keeps non-adjacent selections as separate cuts', () => {
    const plan = resolvePlan(sections(), pick('scene-1', 'scene-3'))
    expect(plan.groups.map((g) => g.numbers)).toEqual([[1], [3]])
    expect(plan.groups.every((g) => g.preservedTransitions === 0)).toBe(true)
  })

  it('does not merge a reversed adjacent pair', () => {
    const plan = resolvePlan(sections(), pick('scene-3', 'scene-2'))
    expect(plan.groups.map((g) => g.numbers)).toEqual([[3], [2]])
  })

  it('a merged cut is one transition longer than the same pair split', () => {
    const merged = resolvePlan(sections(), pick('scene-2', 'scene-3'))
    const split = resolvePlan(sections(), pick('scene-3', 'scene-2'))
    expect(merged.totalSeconds).toBeCloseTo(split.totalSeconds + 0.5, 4)
  })

  it('splits the group when a trim touches the join', () => {
    const all = sections()
    const scene2 = all.find((s) => s.unitId === 'scene-2')!
    const plan = resolvePlan(all, [
      { unitId: 'scene-2', startSeconds: null, endSeconds: scene2.safeEndSeconds - 2 },
      { unitId: 'scene-3', startSeconds: null, endSeconds: null },
    ])
    expect(plan.groups.map((g) => g.numbers)).toEqual([[2], [3]])
  })

  it('clamps a trim into the section safe range', () => {
    const all = sections()
    const scene2 = all.find((s) => s.unitId === 'scene-2')!
    const plan = resolvePlan(all, [
      { unitId: 'scene-2', startSeconds: scene2.startSeconds - 5, endSeconds: 9999 },
    ])
    expect(plan.segments[0]!.startSeconds).toBeCloseTo(scene2.safeStartSeconds, 4)
    expect(plan.segments[0]!.endSeconds).toBeCloseTo(scene2.safeEndSeconds, 4)
  })

  it('flags a clip under the minimum length', () => {
    const all = sections()
    const scene2 = all.find((s) => s.unitId === 'scene-2')!
    const plan = resolvePlan(
      all,
      [
        {
          unitId: 'scene-2',
          startSeconds: scene2.safeStartSeconds,
          endSeconds: scene2.safeStartSeconds + 0.2,
        },
      ],
      0.5,
    )
    expect(plan.segments[0]!.tooShort).toBe(true)
  })

  it('ignores a section the source does not have', () => {
    const plan = resolvePlan(sections(), pick('scene-99', 'scene-1'))
    expect(plan.segments.map((s) => s.unitId)).toEqual(['scene-1'])
  })

  it('totals nothing for an empty selection', () => {
    expect(resolvePlan(sections(), []).totalSeconds).toBe(0)
  })
})

describe('formatting', () => {
  it('renders a readable timecode', () => {
    expect(formatTimecode(0)).toBe('0:00.0')
    expect(formatTimecode(83.45)).toBe('1:23.5')
    expect(formatTimecode(-1)).toBe('—')
  })

  it('converts seconds to a frame number', () => {
    expect(frameAt(1, 60)).toBe(60)
    expect(frameAt(1.5, 30)).toBe(45)
  })
})
