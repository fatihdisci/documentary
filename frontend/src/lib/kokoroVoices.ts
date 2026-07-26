/**
 * Voice-identity rules shared by the Audio tab and the Kokoro panel.
 *
 * Kokoro and the online providers use disjoint voice namespaces, and nothing in
 * the wire types distinguishes them — both are plain strings. These helpers are
 * what stop a project from carrying `af_bella` into Edge (or the reverse),
 * which would otherwise only surface as a provider error at generation time.
 */

import type { KokoroVoiceInfo } from '@/api/audio-types'
import type { TTSProviderName } from '@/api/types'

/** Kokoro voice ids are `<lang><gender>_name`, e.g. `af_bella`, `bm_george`. */
export const KOKORO_VOICE_PATTERN = /^[abefhijpz][fm]_/

/** Mirrors the backend's DEFAULT_VOICE (app/tts/kokoro_catalog.py). */
export const KOKORO_DEFAULT_VOICE = 'af_bella'

/** A known-good Edge voice, used only when nothing better is remembered. */
export const EDGE_FALLBACK_VOICE = 'en-US-AndrewNeural'

export function isKokoroVoice(voiceId: string): boolean {
  return KOKORO_VOICE_PATTERN.test(voiceId.trim())
}

/** Grade order for sorting; anything ungraded sorts last. */
const GRADE_RANK: Record<string, number> = {
  A: 0, 'A-': 1, 'B+': 2, B: 3, 'B-': 4, 'C+': 5, C: 6, 'C-': 7,
  'D+': 8, D: 9, 'D-': 10, 'F+': 11, F: 12,
}

export function gradeRank(grade: string): number {
  return GRADE_RANK[grade] ?? 99
}

export function voiceOptionLabel(voice: KokoroVoiceInfo): string {
  const grade = voice.grade && voice.grade !== '—' ? ` · ${voice.grade}` : ''
  return `${voice.id} — ${voice.label}${grade} · ${voice.gender === 'Female' ? 'Kadın' : 'Erkek'}`
}

/**
 * Keep the selected voice valid for the selected provider.
 *
 * `remembered` is the voice last used with the target provider, so switching
 * away and back restores the choice instead of resetting to a default.
 */
export function voiceForProvider(
  provider: TTSProviderName,
  current: string,
  remembered: string | undefined,
): string {
  if (provider === 'kokoro') {
    if (isKokoroVoice(current)) return current
    return remembered && isKokoroVoice(remembered) ? remembered : KOKORO_DEFAULT_VOICE
  }
  if (provider === 'imported' || !isKokoroVoice(current)) return current
  return remembered && !isKokoroVoice(remembered) ? remembered : EDGE_FALLBACK_VOICE
}
