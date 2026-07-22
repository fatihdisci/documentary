/**
 * A local mirror of the backend's grouping rule, for instant feedback.
 *
 * The backend's plan is authoritative and is what actually gets rendered; this
 * exists so the duration readout and the preview update as the user drags a
 * trim handle, without a round trip per keystroke. The merge rule below is
 * deliberately identical to `app/shorts/plan.py`:
 *
 *   two selections merge into one cut only when they are neighbours on the
 *   source timeline, in that order, and neither is trimmed at the join.
 *
 * Times outside a section's safe range are clamped rather than rejected — the
 * inputs are what stop a bad value being submitted, and the backend validates
 * again before anything is cut.
 */

import type { ShortTimelineSection } from '@/api/shorts-types'

/** Millisecond resolution: below this a "trim" is slider rounding, not intent. */
export const EPSILON = 0.0015

export interface Selection {
  unitId: string
  startSeconds: number | null
  endSeconds: number | null
}

export interface ResolvedSegment {
  unitId: string
  number: number
  title: string
  kind: string
  startSeconds: number
  endSeconds: number
  durationSeconds: number
  trimmed: boolean
  groupIndex: number
  /** True when the resolved window is shorter than the minimum clip length. */
  tooShort: boolean
}

export interface ResolvedGroup {
  index: number
  unitIds: string[]
  numbers: number[]
  startSeconds: number
  endSeconds: number
  durationSeconds: number
  preservedTransitions: number
}

export interface ResolvedPlan {
  segments: ResolvedSegment[]
  groups: ResolvedGroup[]
  totalSeconds: number
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high)
}

export function resolvePlan(
  sections: ShortTimelineSection[],
  selection: Selection[],
  minClipSeconds = 0.5,
): ResolvedPlan {
  const byId = new Map(sections.map((section) => [section.unitId, section]))
  const order = new Map(sections.map((section, index) => [section.unitId, index]))

  const segments: ResolvedSegment[] = []
  const groups: ResolvedGroup[] = []
  let previous: { section: ShortTimelineSection; segment: ResolvedSegment } | null = null

  for (const picked of selection) {
    const section = byId.get(picked.unitId)
    if (!section) continue

    const low = section.safeStartSeconds
    const high = section.safeEndSeconds
    const start = clamp(picked.startSeconds ?? low, low, high)
    const end = clamp(picked.endSeconds ?? high, low, high)
    const duration = Math.max(0, end - start)

    const segment: ResolvedSegment = {
      unitId: section.unitId,
      number: section.number,
      title: section.title,
      kind: section.kind,
      startSeconds: start,
      endSeconds: end,
      durationSeconds: duration,
      trimmed: Math.abs(start - low) > EPSILON || Math.abs(end - high) > EPSILON,
      groupIndex: 0,
      tooShort: duration < minClipSeconds - EPSILON,
    }

    const adjacent =
      previous !== null &&
      (order.get(section.unitId) ?? -1) === (order.get(previous.section.unitId) ?? -2) + 1
    const joinedAtEnd =
      previous !== null &&
      Math.abs(previous.segment.endSeconds - previous.section.safeEndSeconds) <= EPSILON
    const joinedAtStart = Math.abs(start - low) <= EPSILON

    const open = groups[groups.length - 1]
    if (adjacent && joinedAtEnd && joinedAtStart && open) {
      const group = open
      group.endSeconds = end
      group.durationSeconds = group.endSeconds - group.startSeconds
      group.unitIds.push(section.unitId)
      group.numbers.push(section.number)
      group.preservedTransitions += 1
    } else {
      groups.push({
        index: groups.length,
        unitIds: [section.unitId],
        numbers: [section.number],
        startSeconds: start,
        endSeconds: end,
        durationSeconds: duration,
        preservedTransitions: 0,
      })
    }

    segment.groupIndex = groups.length - 1
    segments.push(segment)
    previous = { section, segment }
  }

  const totalSeconds = groups.reduce((sum, group) => sum + group.durationSeconds, 0)
  return { segments, groups, totalSeconds }
}

/** `1:23.4` — readable, and precise enough to talk about a trim. */
export function formatTimecode(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const minutes = Math.floor(seconds / 60)
  const rest = seconds - minutes * 60
  return `${minutes}:${rest.toFixed(1).padStart(4, '0')}`
}

/** How many frames a time is, at the source's rate. Shown beside trim inputs. */
export function frameAt(seconds: number, fps: number): number {
  return Math.round(seconds * fps)
}
