/** Audio, TTS and timing wire types (backend/app/api/audio.py). */

import type { Project } from './project-types'

export interface Voice {
  id: string
  name: string
  locale: string
  gender: string
  description: string
}

export interface TTSProviderStatus {
  name: string
  available: boolean
  message: string
  requiresApiKey: boolean
  apiKeyConfigured: boolean
  supportsRate: boolean
  supportsPitch: boolean
  supportsWordTimings: boolean
  /** True when the provider needs no network connection. */
  offline: boolean
}

export interface UnitResult {
  unitId: string
  label: string
  generated: boolean
  reused: boolean
  durationSeconds: number
  audioFile: string
  audioUrl: string
}

export interface GenerateResponse {
  project: Project
  results: UnitResult[]
  generatedCount: number
  reusedCount: number
  timing: Record<string, unknown>
}

export interface TimelineEntryDto {
  unitId: string
  kind: 'intro' | 'scene' | 'outro'
  index: number
  label: string
  startSeconds: number
  durationSeconds: number
  narrationStartSeconds: number
  narrationEndSeconds: number
  transition: string
  transitionDurationSeconds: number
}

export interface TimingResponse {
  summary: Record<string, number | string>
  entries: TimelineEntryDto[]
  warnings: string[]
  cueCount: number
}
