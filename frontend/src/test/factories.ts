/**
 * Shared fixtures for component tests.
 *
 * A fully-typed default Project plus small builders, so a page test can seed
 * the store with realistic state in one line instead of hand-rolling a partial
 * object that drifts from the wire types.
 */

import type {
  ApiErrorPayload,
} from '@/api/types'
import type {
  AudioSettings,
  ExportSettings,
  MusicSettings,
  Project,
  Scene,
  Section,
  Style,
  SubtitleSettings,
  TextStyle,
  VideoSettings,
} from '@/api/project-types'
import { useProjectStore } from '@/store/project'

function textStyle(overrides: Partial<TextStyle> = {}): TextStyle {
  return {
    fontFamily: 'Inter',
    fontWeight: 700,
    size: 64,
    color: '#FFFFFF',
    letterSpacing: 0,
    lineSpacing: 1.25,
    shadow: true,
    shadowBlur: 12,
    shadowOffset: 3,
    outlineWidth: 0,
    outlineColor: '#000000',
    box: true,
    boxColor: '#000000',
    boxOpacity: 0.45,
    boxPaddingX: 32,
    boxPaddingY: 18,
    boxRadius: 8,
    animation: 'fade',
    fadeInSeconds: 0.5,
    fadeOutSeconds: 0.5,
    maxWidthRatio: 0.62,
    ...overrides,
  }
}

const video: VideoSettings = {
  width: 1920,
  height: 1080,
  fps: 60,
  targetDurationSeconds: 300,
  durationMode: 'audio',
  transitionDurationSeconds: 0.5,
  audioTailSeconds: 2,
  sceneLeadInSeconds: 0.35,
  sceneTailSeconds: 0.65,
  supersampleFactor: 3,
}

const style: Style = {
  fontFamily: 'Inter',
  title: textStyle({ size: 64, fontWeight: 700 }),
  subtitle: textStyle({ size: 36, fontWeight: 400 }),
  caption: textStyle({ size: 38, fontWeight: 500 }),
  subtitles: {
    ...textStyle({ size: 38, fontWeight: 500, maxWidthRatio: 0.8 }),
    maxCharsPerLine: 42,
    maxLines: 2,
    minCueSeconds: 1.2,
    maxCueSeconds: 6,
    maxCharsPerSecond: 17,
  },
  textPosition: 'bottom-left',
  textSafeMargin: 80,
  overlayOpacity: 0.45,
  transitionPreset: 'documentary-dissolve',
  watermarkText: '',
  watermarkOpacity: 0.5,
  showScientificName: true,
}

const audio: AudioSettings = {
  ttsProvider: 'edge',
  voice: 'en-US-GuyNeural',
  speechRate: 1,
  speechPitch: 0,
  voiceVolumeDb: -3,
  musicVolumeDb: -30,
  duckMusicUnderSpeech: true,
  duckStrength: 8,
  musicFadeSeconds: 2.5,
  targetLufs: -16,
  normalizeLoudness: true,
  allowTimeStretch: false,
}

const music: MusicSettings = {
  source: 'none',
  file: null,
  loopIfShort: true,
  introLevelDb: -22,
  outroLevelDb: -22,
}

const subtitles: SubtitleSettings = {
  exportSrt: true,
  exportSceneSrt: true,
  burnIn: false,
}

const exportSettings: ExportSettings = {
  quality: 'youtube-hq',
  intermediateCodec: 'h264-crf12',
  useHardwareEncoder: false,
  exportNarrationAudio: true,
  exportDescription: true,
  keepTempFiles: false,
}

function section(overrides: Partial<Section> = {}): Section {
  return {
    enabled: true,
    useFirstSceneImage: true,
    imageFile: null,
    imagePrompt: '',
    title: '',
    subtitle: '',
    hookText: '',
    narration: '',
    audioFile: null,
    audioSource: 'none',
    audioDurationSeconds: null,
    audioHash: null,
    sceneDurationSeconds: null,
    manualDurationSeconds: null,
    animationPreset: 'auto',
    startScale: 1,
    endScale: 1.08,
    startX: 0.5,
    startY: 0.5,
    endX: 0.5,
    endY: 0.5,
    focusX: 0.5,
    focusY: 0.5,
    transitionPreset: null,
    transitionDurationSeconds: null,
    fadeFromBlackSeconds: 0.8,
    fadeToBlackSeconds: 0.8,
    darkOverlayOpacity: 0.3,
    titleTiming: { startSeconds: 0.6, durationSeconds: 4 },
    subtitleTiming: { startSeconds: 1, durationSeconds: 4 },
    ...overrides,
  }
}

export function makeScene(overrides: Partial<Scene> = {}): Scene {
  return {
    id: Math.random().toString(36).slice(2, 10),
    order: 0,
    enabled: true,
    imageFile: null,
    imagePrompt: '',
    title: '',
    subtitle: '',
    narration: '',
    factNote: '',
    subtitleOverride: null,
    audioFile: null,
    audioSource: 'none',
    audioDurationSeconds: null,
    audioHash: null,
    sceneDurationSeconds: null,
    manualDurationSeconds: null,
    animationPreset: 'auto',
    startScale: 1,
    endScale: 1.08,
    startX: 0.5,
    startY: 0.5,
    endX: 0.5,
    endY: 0.5,
    focusX: 0.5,
    focusY: 0.5,
    fitMode: 'crop',
    rotation: 0,
    transitionPreset: null,
    transitionDurationSeconds: null,
    titleTiming: { startSeconds: 0.6, durationSeconds: 4 },
    subtitleTiming: { startSeconds: 1, durationSeconds: 4 },
    ...overrides,
  }
}

export function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    schemaVersion: 1,
    projectId: 'abc123',
    name: 'The Dodo',
    slug: 'the-dodo',
    animal: { commonName: 'Dodo', scientificName: 'Raphus cucullatus' },
    metadata: { videoTitle: '', description: '', thumbnailText: '', thumbnailPrompt: '', tags: [] },
    video,
    style,
    audio,
    music,
    subtitles,
    export: exportSettings,
    intro: section(),
    outro: section(),
    scenes: [],
    pronunciation: {},
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

/** Seed the project store with a project (or clear it with null). */
export function seedProject(project: Project | null) {
  useProjectStore.setState({
    projects: [],
    project,
    images: [],
    selectedSceneId: project?.scenes[0]?.id ?? null,
    loading: false,
    saveStatus: 'idle',
    lastSavedAt: null,
    error: null,
  })
}

export function apiError(overrides: Partial<ApiErrorPayload> = {}): ApiErrorPayload {
  return {
    code: 'schema_validation',
    message: 'Something went wrong.',
    details: null,
    suggestion: 'Try again.',
    logPath: null,
    context: {},
    ...overrides,
  }
}
