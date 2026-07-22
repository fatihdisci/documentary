/** Shorts wire types (backend/app/shorts/models.py, backend/app/api/shorts.py). */

import type { JobStatus } from './render-types'

export type ShortPhase =
  | 'validate-source' | 'plan' | 'cut-segments' | 'concat'
  | 'compose' | 'validate-output' | 'publish' | 'cleanup'

export type ShortBackgroundStyle = 'black' | 'blurred-background'
export type ShortLayoutStyle = 'centered-fit' | 'title-top' | 'cta-bottom'

export interface ShortLayout {
  width: number
  height: number
  backgroundStyle: ShortBackgroundStyle
  layoutStyle: ShortLayoutStyle
  backgroundColor: string
  groupGapFadeSeconds: number
}

export interface ShortSegmentRequest {
  unitId: string
  startSeconds: number | null
  endSeconds: number | null
}

export interface ShortRequest {
  sourceRenderId: string
  segments: ShortSegmentRequest[]
  layout?: ShortLayout
}

export interface ShortSourceRender {
  renderId: string
  projectSlug: string
  filename: string
  url: string
  createdAt: string
  durationSeconds: number
  width: number
  height: number
  fps: number
  quality: string
  sizeBytes: number
  sectionCount: number
  hasAudio: boolean
  thumbnailUrl: string | null
  status: string
  usable: boolean
  issue: string | null
}

export interface ShortTimelineSection {
  unitId: string
  kind: 'intro' | 'scene' | 'outro'
  number: number
  title: string
  startSeconds: number
  endSeconds: number
  durationSeconds: number
  safeStartSeconds: number
  safeEndSeconds: number
  safeDurationSeconds: number
  transitionToNext: string
  transitionDurationSeconds: number
  transitionFromPreviousSeconds: number
  fadeInSeconds: number
}

export interface ShortSourceTimeline {
  source: ShortSourceRender
  fps: number
  totalDurationSeconds: number
  sections: ShortTimelineSection[]
  minClipSeconds: number
  recommendedMinSeconds: number
  recommendedMaxSeconds: number
  warnSeconds: number
  maxSeconds: number
}

export interface ShortSegmentPlan {
  unitId: string
  number: number
  title: string
  kind: string
  startSeconds: number
  endSeconds: number
  durationSeconds: number
  trimmed: boolean
  groupIndex: number
}

export interface ShortGroupPlan {
  index: number
  startSeconds: number
  endSeconds: number
  durationSeconds: number
  unitIds: string[]
  numbers: number[]
  preservedTransitions: number
}

export interface ShortPlan {
  segments: ShortSegmentPlan[]
  groups: ShortGroupPlan[]
  totalDurationSeconds: number
  cacheKey: string
  warnings: string[]
}

export interface ShortPreviewFrame {
  groupIndex: number
  timeSeconds: number
  url: string
}

export interface ShortsPreflightResponse {
  ready: boolean
  blockingIssues: string[]
  warnings: string[]
  source: ShortSourceRender | null
  plan: ShortPlan | null
  totalDurationSeconds: number
  withinRecommendedBand: boolean
  exceedsContentIdWarning: boolean
  exceedsMaximum: boolean
  recommendedMinSeconds: number
  recommendedMaxSeconds: number
  warnSeconds: number
  maxSeconds: number
  previewFrames: ShortPreviewFrame[]
  cachedShortId: string | null
  activeJobId: string | null
  estimatedRenderSeconds: number
}

export interface ShortArtifact {
  kind: string
  filename: string
  sizeBytes: number
  url: string
}

export interface ShortRecord {
  shortId: string
  projectSlug: string
  filename: string
  url: string
  createdAt: string
  durationSeconds: number
  sizeBytes: number
  width: number
  height: number
  sourceRenderId: string
  sourceVideo: string
  cacheKey: string
  sectionNumbers: number[]
  sectionTitles: string[]
  jobId: string
  artifacts: ShortArtifact[]
}

export interface ShortJob {
  id: string
  projectSlug: string
  request: ShortRequest
  cacheKey: string
  shortId: string
  status: JobStatus
  phase: ShortPhase
  progress: number
  message: string
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  sourceRenderId: string
  sourceVideo: string
  sectionNumbers: number[]
  outputFile: string | null
  artifacts: ShortArtifact[]
  warnings: string[]
  cacheReused: boolean
  errorCode: string | null
  errorMessage: string | null
  errorDetails: string | null
  errorSuggestion: string | null
  logFile: string | null
  totalDurationSeconds: number
  segmentCount: number
  groupCount: number
}

export interface ShortJobEvent {
  jobId: string
  status: JobStatus
  phase: ShortPhase
  progress: number
  message: string
  elapsedSeconds: number
  estimatedRemainingSeconds: number | null
  errorCode: string | null
  errorMessage: string | null
  errorSuggestion: string | null
}
