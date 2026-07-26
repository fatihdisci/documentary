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

export interface KokoroVoiceInfo {
  id: string
  label: string
  gender: string
  /** The model author's listening grade, e.g. "A", "C+". */
  grade: string
  training: string
  note: string
  langCode: string
  language: string
  locale: string
  /** False for languages Kokoro cannot time at word level. */
  wordTimings: boolean
}

export interface KokoroLanguageInfo {
  code: string
  label: string
  locale: string
  extraInstall: string
  wordTimings: boolean
  voiceCount: number
}

export interface KokoroEnvironment {
  installed: boolean
  modelCached: boolean
  espeakAvailable: boolean
  device: string
  cacheDir: string
  pipInstall: string
  espeakInstall: string
  repoId: string
  sampleRate: number
  defaultVoice: string
  torchVersion: string
}

export interface KokoroInfo {
  status: TTSProviderStatus
  environment: KokoroEnvironment
  voices: KokoroVoiceInfo[]
  languages: KokoroLanguageInfo[]
  recommended: string[]
  deviceOptions: string[]
  minSpeed: number
  maxSpeed: number
  setupSteps: string[]
  usageNotes: string[]
  inputNotes: string[]
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
