/** Project schema types, mirroring backend/app/models/project.py. */

import type {
  AnimationPreset,
  DurationMode,
  IntermediateCodec,
  MusicSource,
  QualityPreset,
  TransitionPreset,
  TTSProviderName,
} from './types'

export type AudioSource = 'none' | 'generated' | 'imported'
export type FitMode = 'fill' | 'fit' | 'crop'
export type TextAnimation = 'none' | 'fade' | 'slide-up' | 'slide-left'
export type TextPosition =
  | 'top-left' | 'top-center' | 'top-right'
  | 'middle-left' | 'middle-center' | 'middle-right'
  | 'bottom-left' | 'bottom-center' | 'bottom-right'

export interface Animal {
  commonName: string
  scientificName: string
}

export interface ProjectMetadata {
  videoTitle: string
  description: string
  thumbnailText: string
  thumbnailPrompt: string
  tags: string[]
}

export interface VideoSettings {
  width: number
  height: number
  fps: number
  targetDurationSeconds: number
  durationMode: DurationMode
  transitionDurationSeconds: number
  audioTailSeconds: number
  sceneLeadInSeconds: number
  sceneTailSeconds: number
  supersampleFactor: number
}

export interface TextStyle {
  fontFamily: string
  fontWeight: number
  size: number
  color: string
  letterSpacing: number
  lineSpacing: number
  shadow: boolean
  shadowBlur: number
  shadowOffset: number
  outlineWidth: number
  outlineColor: string
  box: boolean
  boxColor: string
  boxOpacity: number
  boxPaddingX: number
  boxPaddingY: number
  boxRadius: number
  animation: TextAnimation
  fadeInSeconds: number
  fadeOutSeconds: number
  maxWidthRatio: number
}

export interface SubtitleStyle extends TextStyle {
  maxCharsPerLine: number
  maxLines: number
  minCueSeconds: number
  maxCueSeconds: number
  maxCharsPerSecond: number
}

export interface Style {
  fontFamily: string
  title: TextStyle
  subtitle: TextStyle
  caption: TextStyle
  subtitles: SubtitleStyle
  textPosition: TextPosition
  textSafeMargin: number
  overlayOpacity: number
  transitionPreset: TransitionPreset
  watermarkText: string
  watermarkOpacity: number
  showScientificName: boolean
}

export interface AudioSettings {
  ttsProvider: TTSProviderName
  voice: string
  speechRate: number
  speechPitch: number
  voiceVolumeDb: number
  musicVolumeDb: number
  duckMusicUnderSpeech: boolean
  duckStrength: number
  musicFadeSeconds: number
  targetLufs: number
  normalizeLoudness: boolean
  allowTimeStretch: boolean
}

export interface MusicSettings {
  source: MusicSource
  file: string | null
  loopIfShort: boolean
  introLevelDb: number
  outroLevelDb: number
}

export interface SubtitleSettings {
  exportSrt: boolean
  exportSceneSrt: boolean
  burnIn: boolean
}

export interface TextTiming {
  startSeconds: number
  durationSeconds: number
}

export interface Scene {
  id: string
  order: number
  enabled: boolean
  imageFile: string | null
  imagePrompt: string
  title: string
  subtitle: string
  narration: string
  factNote: string
  subtitleOverride: string[] | null
  audioFile: string | null
  audioSource: AudioSource
  audioDurationSeconds: number | null
  audioHash: string | null
  sceneDurationSeconds: number | null
  manualDurationSeconds: number | null
  animationPreset: AnimationPreset
  startScale: number
  endScale: number
  startX: number
  startY: number
  endX: number
  endY: number
  focusX: number
  focusY: number
  fitMode: FitMode
  rotation: number
  transitionPreset: TransitionPreset | null
  transitionDurationSeconds: number | null
  titleTiming: TextTiming
  subtitleTiming: TextTiming
}

export interface Section {
  enabled: boolean
  useFirstSceneImage: boolean
  imageFile: string | null
  imagePrompt: string
  title: string
  subtitle: string
  hookText: string
  narration: string
  audioFile: string | null
  audioSource: AudioSource
  audioDurationSeconds: number | null
  audioHash: string | null
  sceneDurationSeconds: number | null
  manualDurationSeconds: number | null
  animationPreset: AnimationPreset
  startScale: number
  endScale: number
  startX: number
  startY: number
  endX: number
  endY: number
  focusX: number
  focusY: number
  transitionPreset: TransitionPreset | null
  transitionDurationSeconds: number | null
  fadeFromBlackSeconds: number
  fadeToBlackSeconds: number
  darkOverlayOpacity: number
  titleTiming: TextTiming
  subtitleTiming: TextTiming
}

export interface ExportSettings {
  quality: QualityPreset
  intermediateCodec: IntermediateCodec
  useHardwareEncoder: boolean
  exportNarrationAudio: boolean
  exportDescription: boolean
  keepTempFiles: boolean
}

export interface Project {
  schemaVersion: number
  projectId: string
  name: string
  slug: string
  animal: Animal
  metadata: ProjectMetadata
  video: VideoSettings
  style: Style
  audio: AudioSettings
  music: MusicSettings
  subtitles: SubtitleSettings
  export: ExportSettings
  intro: Section
  scenes: Scene[]
  outro: Section
  pronunciation: Record<string, string>
  createdAt: string
  updatedAt: string
}

export interface ProjectSummary {
  slug: string
  projectId: string
  name: string
  commonName: string
  sceneCount: number
  updatedAt: string
  archived: boolean
  hasImages: boolean
  thumbnailUrl: string | null
}

export interface MusicTrack {
  filename: string
  sizeBytes: number
}

/** The render's resolved Ken Burns move for one unit (backend `SceneMotion`). */
export interface SceneMotion {
  unitId: string
  kind: 'intro' | 'scene' | 'outro'
  preset: string
  isStatic: boolean
  startScale: number
  endScale: number
  startX: number
  startY: number
  endX: number
  endY: number
  description: string
}

export interface ImageInfo {
  filename: string
  width: number
  height: number
  format: string
  sizeBytes: number
  aspectRatio: number
  thumbnailUrl: string | null
  warnings: string[]
}

export interface ProjectResponse {
  project: Project
  images: ImageInfo[]
}

export interface ImportReport {
  scenesCreated: number
  scenesUpdated: number
  scenesRemoved: number
  imagesMapped: number
  unmappedScenes: number[]
  unusedImages: string[]
  warnings: string[]
}

export interface ImageMapping {
  imagesMapped: number
  unmappedScenes: number[]
  unusedImages: string[]
  warnings: string[]
}

export interface UploadImagesResponse {
  images: ImageInfo[]
  mapping: ImageMapping | null
}

export interface ImportContentResponse {
  project: Project
  report: ImportReport
}
