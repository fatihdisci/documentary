/**
 * Wire types mirroring the backend Pydantic models (camelCase on both sides).
 *
 * Kept hand-written and narrow rather than generated, so the compiler catches
 * drift the moment a field is renamed on either side.
 */

export type CheckStatus = 'ok' | 'warn' | 'fail'

export interface DiagnosticCheck {
  id: string
  label: string
  status: CheckStatus
  value: string
  detail: string
  suggestion: string
}

export interface DiagnosticsReport {
  generatedAt: number
  healthy: boolean
  checks: DiagnosticCheck[]
  notes: string[]
}

/** The structured error payload every failing endpoint returns. */
export interface ApiErrorPayload {
  code: string
  message: string
  details: string | null
  suggestion: string
  logPath: string | null
  context: Record<string, unknown>
}

export type TTSProviderName = 'edge' | 'imported' | 'elevenlabs'
export type MusicSource = 'none' | 'uploaded' | 'generated-ambient'
export type QualityPreset = 'preview' | 'standard' | 'high' | 'youtube-hq'
export type DurationMode = 'audio' | 'target' | 'manual'
export type IntermediateCodec = 'h264-crf12' | 'h264-crf14-fast' | 'prores-lt' | 'prores-422'

export type TransitionPreset =
  | 'none'
  | 'cross-dissolve'
  | 'documentary-dissolve'
  | 'slow-cinematic-dissolve'
  | 'fade-through-black'
  | 'fade-through-white'
  | 'dip-to-black'
  | 'subtle-zoom-dissolve'
  | 'horizontal-slide'
  | 'vertical-slide'
  | 'blur-dissolve'

export type AnimationPreset =
  | 'auto'
  | 'slow-zoom-in'
  | 'slow-zoom-out'
  | 'pan-left-to-right'
  | 'pan-right-to-left'
  | 'pan-top-to-bottom'
  | 'pan-bottom-to-top'
  | 'zoom-to-center'
  | 'zoom-to-left'
  | 'zoom-to-right'
  | 'zoom-to-focus'
  | 'gentle-diagonal'
  | 'static'

export interface AppSettings {
  ffmpegPath: string
  ffprobePath: string
  projectsDir: string
  exportsDir: string
  tempDir: string
  ttsProvider: TTSProviderName
  defaultVoice: string
  defaultFont: string
  defaultFps: number
  defaultWidth: number
  defaultHeight: number
  defaultTransition: TransitionPreset
  defaultSceneLeadInSeconds: number
  defaultSceneTailSeconds: number
  defaultQuality: QualityPreset
  intermediateCodec: IntermediateCodec
  useHardwareEncoder: boolean
  cleanupTempOnSuccess: boolean
  tempRetentionDays: number
  logLevel: string
  maxUploadMb: number
  maxJsonMb: number
  diskSafetyMarginMb: number
}

export interface SettingsResponse {
  settings: AppSettings
  configuredSecrets: string[]
  resolvedPaths: Record<string, string>
}
