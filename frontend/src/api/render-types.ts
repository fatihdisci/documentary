/** Render job and export wire types (backend/app/api/render.py). */

import type { QualityPreset } from './types'

export type JobStatus =
  | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted'

export type JobPhase =
  | 'validate' | 'verify-sources' | 'generate-tts' | 'probe-audio'
  | 'compute-timeline' | 'build-subtitles' | 'normalize-images'
  | 'render-text-cards' | 'render-scene-clips' | 'assemble' | 'mix-audio'
  | 'encode' | 'validate-output' | 'write-artifacts' | 'cleanup'

export interface JobArtifact {
  kind: string
  filename: string
  sizeBytes: number
  url: string
}

export interface RenderJob {
  id: string
  projectSlug: string
  status: JobStatus
  phase: JobPhase
  quality: QualityPreset
  progress: number
  message: string
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  outputFile: string | null
  artifacts: JobArtifact[]
  warnings: string[]
  errorCode: string | null
  errorMessage: string | null
  errorDetails: string | null
  errorSuggestion: string | null
  logFile: string | null
  totalDurationSeconds: number
  scenesRendered: number
  scenesReused: number
}

export interface JobEvent {
  jobId: string
  status: JobStatus
  phase: JobPhase
  progress: number
  message: string
  elapsedSeconds: number
  estimatedRemainingSeconds: number | null
  errorCode: string | null
  errorMessage: string | null
  errorSuggestion: string | null
}

export interface TransitionInfo {
  sceneId: string
  preset: string
  label: string
  restrained: boolean
  durationSeconds: number
}

export interface PreflightResponse {
  ready: boolean
  blockingIssues: string[]
  warnings: string[]
  timing: Record<string, number | string>
  disk: Record<string, number | boolean>
  transitions: TransitionInfo[]
  estimatedRenderSeconds: number
}

export interface ExportEntry {
  filename: string
  sizeBytes: number
  modifiedAt: number
  url: string
}
