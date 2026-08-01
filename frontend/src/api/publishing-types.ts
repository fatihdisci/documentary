/**
 * Publishing wire types, mirroring backend/app/publishing/models.py.
 *
 * Hand-written and narrow like the rest of `src/api`, so a rename on either side
 * is a compile error rather than a runtime surprise.
 */

import type { JobStatus } from './render-types'

/** Both publish for real. What differs is which connection each one needs. */
export type PublishingPlatform = 'youtube' | 'tiktok'

/** The one that uses the caption/hashtags shape rather than YouTube's fields. */
export type SocialPlatform = Exclude<PublishingPlatform, 'youtube'>

export type MediaKind = 'long' | 'short'
export type PrivacyStatus = 'private' | 'unlisted' | 'public'
export type PublishMode = 'now' | 'schedule'
export type AssetStatus = 'skipped' | 'pending' | 'uploaded' | 'failed'

export type PublishPhase =
  | 'validate'
  | 'authenticate'
  | 'hash-source'
  | 'upload-video'
  | 'set-thumbnail'
  | 'upload-captions'
  | 'create-container'
  | 'await-processing'
  | 'fetch-status'
  | 'complete'

export interface SourceFingerprint {
  filename: string
  sizeBytes: number
  sha256: string
}

export interface MediaItem {
  mediaId: string
  kind: MediaKind
  filename: string
  url: string
  projectSlug: string
  projectName: string
  createdAt: string
  durationSeconds: number
  sizeBytes: number
  width: number
  height: number
  fps: number
  quality: string
  thumbnailUrl: string | null
  recommended: boolean
  note: string | null
  fingerprint: SourceFingerprint
  captionFilename: string | null
  captionUrl: string | null
  hasDraft: boolean
  publishedVideoId: string | null
  contentPlanId: string | null
}

export interface YouTubeDraft {
  title: string
  description: string
  tags: string[]
  categoryId: string
  defaultLanguage: string
  defaultAudioLanguage: string
  privacyStatus: PrivacyStatus
  publishMode: PublishMode
  /** Local wall-clock time in Europe/Istanbul, e.g. `2026-08-01T22:00`. */
  publishAtLocal: string | null
  madeForKids: boolean
  notifySubscribers: boolean
  embeddable: boolean
  thumbnailFile: string | null
  captionFile: string | null
  captionSource: string
  captionLanguage: string
  captionName: string
  captionIsDraft: boolean
  uploadCaptions: boolean
}

export interface SocialDraft {
  caption: string
  hashtags: string[]
  /** The user's own note. Never authorizes anything — the connection decides. */
  account: string
  publishMode: PublishMode
  publishAtLocal: string | null
}

export interface TikTokDraft extends SocialDraft {
  /** Mirrors TikTok's `privacy_level`, e.g. `SELF_ONLY`. */
  privacy: string
  allowComments: boolean
  allowDuet: boolean
  allowStitch: boolean
}

export interface CommonDraft {
  title: string
  description: string
  tags: string[]
  /** Reference only; never sent to any platform. */
  thumbnailText: string
  thumbnailPrompt: string
  /**
   * The Short's opening hook and a one-line summary of the long video's branded
   * opening. Both reference only, and both absent from a backend that predates
   * them. The hook is already burned into a rendered Short, so editing it here
   * is a note for the next cut.
   */
  hookText?: string
  longIntroSummary?: string
}

export interface PublishDraft {
  mediaId: string
  projectSlug: string
  sourceFingerprint: SourceFingerprint
  common: CommonDraft
  youtube: YouTubeDraft
  tiktok: TikTokDraft
  updatedAt: string
}

export interface PublishHistoryEntry {
  entryId: string
  jobId: string
  projectSlug: string
  mediaId: string
  platform: PublishingPlatform
  filename: string
  title: string
  videoId: string
  videoUrl: string
  uploadedAt: string
  requestedPublishAt: string | null
  actualPublishAt: string | null
  privacyStatus: string
  uploadStatus: string | null
  processingStatus: string | null
  thumbnailStatus: AssetStatus
  captionStatus: AssetStatus
  source: SourceFingerprint
  warnings: string[]
}

export interface DraftResponse {
  draft: PublishDraft
  media: MediaItem
  sourceChanged: boolean
  sourceChangedReason: string | null
  /** The YouTube duplicate, kept separate because the header warning reads it. */
  duplicateOf: PublishHistoryEntry | null
  /** The same check per platform. A post on TikTok says nothing about YouTube. */
  duplicates: Partial<Record<PublishingPlatform, PublishHistoryEntry>>
}

export interface PublishJob {
  id: string
  projectSlug: string
  mediaId: string
  platform: PublishingPlatform
  source: SourceFingerprint
  status: JobStatus
  phase: PublishPhase
  progress: number
  message: string
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  videoId: string | null
  videoUrl: string | null
  /** An ingestion handle that exists before anything is public. */
  containerId: string | null
  title: string
  requestedPrivacyStatus: PrivacyStatus
  requestedPublishAt: string | null
  actualPrivacyStatus: string | null
  actualPublishAt: string | null
  uploadStatus: string | null
  processingStatus: string | null
  thumbnailStatus: AssetStatus
  thumbnailError: string | null
  captionStatus: AssetStatus
  captionError: string | null
  captionTrackId: string | null
  uploadedBytes: number
  totalBytes: number
  warnings: string[]
  errorCode: string | null
  errorMessage: string | null
  errorDetails: string | null
  errorSuggestion: string | null
}

export interface PublishJobEvent {
  jobId: string
  status: JobStatus
  phase: PublishPhase
  progress: number
  message: string
  elapsedSeconds: number
  estimatedRemainingSeconds: number | null
  uploadedBytes: number
  totalBytes: number
  videoId: string | null
  videoUrl: string | null
  thumbnailStatus: AssetStatus
  captionStatus: AssetStatus
  errorCode: string | null
  errorMessage: string | null
  errorSuggestion: string | null
}

/** Connection state for the Settings page. Carries no credential. */
export interface YouTubeConnection {
  clientFilePresent: boolean
  clientFileName: string | null
  availableClientFiles: string[]
  tokenPresent: boolean
  connected: boolean
  needsReconnect: boolean
  expired: boolean
  scopesSufficient: boolean
  missingScopes: string[]
  channelId: string | null
  channelTitle: string | null
  channelThumbnailUrl: string | null
  checkedAt: string | null
  statusMessage: string
  problem: string | null
  suggestion: string | null
}

export interface ClientSecretUploadResponse {
  connection: YouTubeConnection
  storedFileName: string
}

export interface TikTokCreatorInfo {
  nickname: string
  username: string
  avatarUrl: string | null
  privacyLevelOptions: string[]
  commentDisabled: boolean
  duetDisabled: boolean
  stitchDisabled: boolean
  maxVideoPostDurationSeconds: number
  fetchedAt: string | null
}

export interface TikTokConnection {
  appConfigured: boolean
  tokenPresent: boolean
  connected: boolean
  needsReconnect: boolean
  expired: boolean
  expiresAt: string | null
  scopesSufficient: boolean
  missingScopes: string[]
  displayName: string | null
  avatarUrl: string | null
  creatorInfo: TikTokCreatorInfo | null
  /** True until the app passes TikTok's audit; public posting is unavailable. */
  auditRequired: boolean
  redirectUri: string
  checkedAt: string | null
  statusMessage: string
  problem: string | null
  suggestion: string | null
}

export interface TikTokAppCredentials {
  clientKey: string
  clientSecret: string
  replace: boolean
}

/** Where to send the browser. The only place an app id appears. */
export interface OAuthStart {
  authorizationUrl: string
  redirectUri: string
}

/** TikTok's limit, restated so the UI can count without asking. */
export const MAX_TIKTOK_TITLE_CHARS = 2200

/** Mirrors `service.compose_caption`: what one post actually carries. */
export function composeCaption(caption: string, hashtags: string[]): string {
  const tags = hashtags
    .map((tag) => tag.trim())
    .filter(Boolean)
    .map((tag) => (tag.startsWith('#') ? tag : `#${tag.replace(/\s+/g, '')}`))
  const text = caption.trim()
  return tags.length === 0 ? text : `${text}\n\n${tags.join(' ')}`.trim()
}

export interface AssetUploadResponse {
  filename: string
  url: string
}

export interface PublishRequest {
  mediaId: string
  allowDuplicate: boolean
}

/** YouTube's limits, restated so the UI can count without asking the backend. */
export const MAX_TITLE_CHARS = 100
export const MAX_DESCRIPTION_BYTES = 5000
export const MAX_TAGS_LENGTH = 500

/** How long YouTube considers a tag list to be; mirrors `service.tags_length`. */
export function tagsLength(tags: string[]): number {
  if (tags.length === 0) return 0
  const total = tags.reduce((sum, tag) => sum + tag.length + (tag.includes(' ') ? 2 : 0), 0)
  return total + Math.max(0, tags.length - 1)
}

/** UTF-8 byte length, which is the unit YouTube limits descriptions in. */
export function utf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length
}
